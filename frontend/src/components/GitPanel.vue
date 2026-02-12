<script setup>
import { ref, computed, watch } from 'vue'
import { apiFetch } from '../utils/api'
import { useSettingsStore } from '../stores/settings'
import {
    GitLog,
    GitLogGraphHTMLGrid,
    GitLogTable,
    GitLogTags,
} from './GitLog'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
    sessionId: {
        type: String,
        required: true,
    },
    active: {
        type: Boolean,
        default: false,
    },
})

const settingsStore = useSettingsStore()

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const started = ref(false)
const loading = ref(false)
const error = ref(null)
const entries = ref([])
const currentBranch = ref('')
const headCommitHash = ref('')
const indexStatus = ref(null)
const hasMore = ref(false)

// ---------------------------------------------------------------------------
// Theme — follow app-wide effective theme
// ---------------------------------------------------------------------------

const themeMode = computed(() => settingsStore.getEffectiveTheme)

// Use a palette that matches the current theme
const colours = computed(() =>
    themeMode.value === 'dark' ? 'neon-aurora-dark' : 'neon-aurora-light'
)

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

const apiUrl = computed(() =>
    `/api/projects/${props.projectId}/sessions/${props.sessionId}/git-log/`
)

async function fetchGitLog() {
    loading.value = true
    error.value = null

    try {
        const res = await apiFetch(apiUrl.value)

        if (!res.ok) {
            const data = await res.json().catch(() => ({}))
            error.value = data.error || `Request failed (${res.status})`
            return
        }

        const data = await res.json()
        entries.value = data.entries || []
        currentBranch.value = data.current_branch || ''
        headCommitHash.value = data.head_commit_hash || ''
        indexStatus.value = data.index_status || null
        hasMore.value = data.has_more || false
    } catch (e) {
        error.value = 'Failed to load git history'
    } finally {
        loading.value = false
    }
}

// ---------------------------------------------------------------------------
// Lazy init: fetch only when the tab becomes active for the first time
// ---------------------------------------------------------------------------

watch(
    () => props.active,
    (active) => {
        if (active && !started.value) {
            started.value = true
            fetchGitLog()
        }
    },
    { immediate: true },
)
</script>

<template>
    <div class="git-panel">
        <!-- Loading state -->
        <div v-if="loading" class="panel-state">
            <wa-spinner></wa-spinner>
            <span>Loading git history...</span>
        </div>

        <!-- Error state -->
        <div v-else-if="error" class="panel-state">
            <wa-callout variant="danger" appearance="outlined">
                <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
                <div class="error-content">
                    <div>{{ error }}</div>
                    <wa-button
                        variant="danger"
                        appearance="outlined"
                        size="small"
                        @click="fetchGitLog"
                    >
                        <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                        Retry
                    </wa-button>
                </div>
            </wa-callout>
        </div>

        <!-- Empty state (no commits) -->
        <div v-else-if="started && entries.length === 0" class="panel-state">
            <span class="panel-placeholder">No commits found</span>
        </div>

        <!-- GitLog visualization -->
        <template v-else-if="entries.length > 0">
            <div v-if="hasMore" class="has-more-banner">
                <wa-icon name="circle-info"></wa-icon>
                Only the last 200 commits are shown.
            </div>
            <div class="gitlog-container">
                <GitLog
                    :entries="entries"
                    :current-branch="currentBranch"
                    :head-commit-hash="headCommitHash"
                    :index-status="indexStatus"
                    :theme="themeMode"
                    :colours="colours"
                    :show-headers="false"
                    :node-size=10
                    :row-height=28
                >
                    <template #tags>
                        <GitLogTags />
                    </template>
                    <template #graph>
                        <GitLogGraphHTMLGrid
                            :show-commit-node-tooltips="true"
                            :show-commit-node-hashes="false"
                        />
                    </template>
                    <template #table>
                        <GitLogTable timestamp-format="YYYY-MM-DD HH:mm" />
                    </template>
                </GitLog>
            </div>
        </template>
    </div>
</template>

<style scoped>
.git-panel {
    height: 100%;
    display: flex;
    flex-direction: column;
}

.panel-state {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.panel-placeholder {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.error-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    text-align: center;
}

.has-more-banner {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-s);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    border-bottom: 1px solid var(--wa-color-surface-border);
}

.gitlog-container {
    flex: 1;
    min-height: 0;
    overflow: hidden;
}
</style>
