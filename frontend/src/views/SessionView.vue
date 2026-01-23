<script setup>
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useDataStore } from '../stores/data'
import SessionItem from '../components/SessionItem.vue'

const route = useRoute()
const store = useDataStore()

// Current session from route params
const projectId = computed(() => route.params.projectId)
const sessionId = computed(() => route.params.sessionId)

// Session data
const session = computed(() => store.getSession(sessionId.value))

// Session items
const items = computed(() => store.getSessionItems(sessionId.value))

// Loading state
const isLoading = computed(() => store.areSessionItemsLoading(sessionId.value))

// Load session items when session changes
watch([projectId, sessionId], async ([newProjectId, newSessionId]) => {
    if (newProjectId && newSessionId) {
        await store.loadSessionItems(newProjectId, newSessionId)
    }
}, { immediate: true })

// Format mtime as local datetime
function formatDate(timestamp) {
    if (!timestamp) return '-'
    const date = new Date(timestamp * 1000)
    return date.toLocaleString()
}

// Truncate session ID for display in header
function truncateId(id) {
    if (!id || id.length <= 20) return id
    return id.slice(0, 8) + '...' + id.slice(-8)
}
</script>

<template>
    <div class="session-view">
        <!-- Header -->
        <header class="session-header" v-if="session">
            <div class="session-title">
                <h2 :title="sessionId">{{ truncateId(sessionId) }}</h2>
            </div>
            <div class="session-meta">
                <span class="meta-item">
                    <wa-icon name="list-numbers"></wa-icon>
                    {{ session.last_line }} lines
                </span>
                <span class="meta-item">
                    <wa-icon name="clock"></wa-icon>
                    {{ formatDate(session.mtime) }}
                </span>
            </div>
        </header>

        <wa-divider></wa-divider>

        <!-- Items list -->
        <div class="session-items">
            <div
                v-for="(item, index) in items"
                :key="item.line_num"
                class="item-wrapper"
            >
                <SessionItem
                    :content="item.content"
                    :line-num="item.line_num"
                />
                <wa-divider v-if="index < items.length - 1"></wa-divider>
            </div>
            <div v-if="isLoading" class="empty-state">
                <wa-spinner></wa-spinner>
                <span>Loading...</span>
            </div>
            <div v-else-if="items.length === 0" class="empty-state">
                No items in this session
            </div>
        </div>
    </div>
</template>

<style scoped>
.session-view {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
}

.session-header {
    flex-shrink: 0;
    padding: var(--wa-space-l);
}

.session-view > wa-divider {
    flex-shrink: 0;
}

.session-title h2 {
    margin: 0;
    font-size: var(--wa-font-size-l);
    font-weight: 600;
    font-family: var(--wa-font-mono);
    color: var(--wa-color-text);
}

.session-meta {
    display: flex;
    gap: var(--wa-space-l);
    margin-top: var(--wa-space-s);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-subtle);
}

.meta-item {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.session-items {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: var(--wa-space-l);
}

.item-wrapper {
    margin-bottom: var(--wa-space-m);
}

.item-wrapper wa-divider {
    margin-top: var(--wa-space-m);
}

.empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 200px;
    color: var(--wa-color-text-subtle);
    font-size: var(--wa-font-size-m);
}
</style>
