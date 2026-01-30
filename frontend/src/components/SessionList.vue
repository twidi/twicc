<script setup>
import { computed } from 'vue'
import { useDataStore } from '../stores/data'
import { formatDate } from '../utils/date'

const props = defineProps({
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        default: null
    }
})

const store = useDataStore()

// Sessions are already sorted by mtime desc in the getter
const sessions = computed(() => {
    return store.getProjectSessions(props.projectId)
})

// Get display name for session (title if available, otherwise ID)
function getSessionDisplayName(session) {
    return session.title || session.id
}

// Format cost as USD string (e.g., "$0.42")
function formatCost(cost) {
    if (cost == null) return null
    return `$${cost.toFixed(2)}`
}

const emit = defineEmits(['select'])

function handleSelect(session) {
    emit('select', session)
}
</script>

<template>
    <div class="session-list">
        <div
            v-for="session in sessions"
            :key="session.id"
            class="session-item"
            :class="{ 'session-item--active': session.id === sessionId }"
            @click="handleSelect(session)"
        >
            <div class="session-name" :title="session.title || session.id">{{ getSessionDisplayName(session) }}</div>
            <div class="session-meta">
                <span class="session-messages"><wa-icon name="comment" variant="regular"></wa-icon> {{ session.message_count ?? '??' }}</span>
                <span v-if="session.total_cost != null" class="session-cost"><wa-icon name="coins" variant="regular"></wa-icon> {{ formatCost(session.total_cost) }}</span>
                <span class="session-mtime"><wa-icon name="clock" variant="regular"></wa-icon> {{ formatDate(session.mtime, { smart: true }) }}</span>
            </div>
        </div>
        <div v-if="sessions.length === 0" class="empty-state">
            No sessions
        </div>
    </div>
</template>

<style scoped>
.session-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.session-item {
    padding: var(--wa-space-s) var(--wa-space-m);
    border-radius: var(--wa-radius-s);
    cursor: pointer;
    transition: background-color 0.15s ease;
    background: transparent;
}

.session-item:hover {
    background: var(--wa-color-surface-lowered);
}

.session-item--active {
    background: var(--wa-color-surface-active);
    border-left: 3px solid var(--wa-color-brand);
}

.session-name {
    font-size: var(--wa-font-size-s);
    font-weight: 600;
    color: var(--wa-color-text);
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.session-meta {
    display: flex;
    justify-content: space-between;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    margin-top: var(--wa-space-2xs);
}

.empty-state {
    text-align: center;
    padding: var(--wa-space-l);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
</style>
