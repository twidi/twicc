<script setup>
import { computed } from 'vue'
import { useDataStore } from '../stores/data'

const props = defineProps({
    projectId: {
        type: String,
        required: true
    },
    activeSessionId: {
        type: String,
        default: null
    }
})

const store = useDataStore()

// Sessions are already sorted by mtime desc in the getter
const sessions = computed(() => {
    return store.getProjectSessions(props.projectId)
})

// Format mtime as local datetime
function formatDate(timestamp) {
    if (!timestamp) return '-'
    const date = new Date(timestamp * 1000)
    return date.toLocaleString()
}

// Truncate session ID for display
function truncateId(id) {
    if (id.length <= 12) return id
    return id.slice(0, 8) + '...'
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
            :class="{ 'session-item--active': session.id === activeSessionId }"
            @click="handleSelect(session)"
        >
            <div class="session-id" :title="session.id">{{ truncateId(session.id) }}</div>
            <div class="session-meta">
                <span class="session-lines">{{ session.last_line }} lines</span>
                <span class="session-mtime">{{ formatDate(session.mtime) }}</span>
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
    background: var(--wa-color-surface-alt);
}

.session-item--active {
    background: var(--wa-color-surface-active);
    border-left: 3px solid var(--wa-color-brand);
}

.session-id {
    font-size: var(--wa-font-size-s);
    font-family: var(--wa-font-mono);
    font-weight: 500;
    color: var(--wa-color-text);
}

.session-meta {
    display: flex;
    justify-content: space-between;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-subtle);
    margin-top: var(--wa-space-2xs);
}

.empty-state {
    text-align: center;
    padding: var(--wa-space-l);
    color: var(--wa-color-text-subtle);
    font-size: var(--wa-font-size-s);
}
</style>
