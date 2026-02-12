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
import GitPanelHeader from './GitPanelHeader.vue'

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
const hasMore = ref(false)

/**
 * Index changed files data from the git-log response.
 * Shape: { stats: { modified, added, deleted }, tree: { name, type, loaded, children } }
 * or null if no changes.
 */
const indexFilesData = ref(null)

/** Counts from index — passed to GitLog's indexStatus prop. */
const indexStatus = computed(() => indexFilesData.value?.stats ?? null)

// ---------------------------------------------------------------------------
// Git log overlay toggle & commit selection
// ---------------------------------------------------------------------------

const gitLogOpen = ref(false)
const selectedCommit = ref(null)

/**
 * Commit changed files data fetched from git-commit-files endpoint.
 * Shape: { stats: { modified, added, deleted }, tree: { name, type, loaded, children } }
 */
const commitFilesData = ref(null)
const commitFilesLoading = ref(false)

/** Stats for the header: commit-specific when a commit is selected, index otherwise. */
const headerStats = computed(() => {
    if (!selectedCommit.value || selectedCommit.value.hash === 'index') {
        return indexFilesData.value?.stats ?? null
    }
    return commitFilesData.value?.stats ?? null
})

function toggleGitLog() {
    gitLogOpen.value = !gitLogOpen.value
}

function onCommitSelected(commit) {
    selectedCommit.value = commit || null
    if (commit) {
        gitLogOpen.value = false
    }
}

// ---------------------------------------------------------------------------
// Fetch commit files when a commit is selected
// ---------------------------------------------------------------------------

watch(selectedCommit, async (commit) => {
    // Reset data
    commitFilesData.value = null

    if (!commit || commit.hash === 'index') {
        return
    }

    commitFilesLoading.value = true
    try {
        const url = `/api/projects/${props.projectId}/sessions/${props.sessionId}/git-commit-files/${commit.hash}/`
        const res = await apiFetch(url)

        if (res.ok) {
            commitFilesData.value = await res.json()
        }
    } catch {
        // Silently ignore — header will just not show stats
    } finally {
        commitFilesLoading.value = false
    }
})

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
        indexFilesData.value = data.index_files || null
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

        <!-- Main content: header + git log overlay -->
        <template v-else-if="entries.length > 0">
            <!-- Header with commit selector -->
            <GitPanelHeader
                :selected-commit="selectedCommit"
                :stats="headerStats"
                :stats-loading="commitFilesLoading"
                :git-log-open="gitLogOpen"
                @toggle-git-log="toggleGitLog"
            />
            <wa-divider></wa-divider>

            <!-- Content area (position: relative so overlay can cover it) -->
            <div class="git-panel-content">
                <!-- Future: split panel with file list on right and Monaco editor on left -->

                <!-- Git log overlay (absolute, shown when chevron is clicked) -->
                <div v-if="gitLogOpen" class="gitlog-overlay">
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
                        :on-select-commit="onCommitSelected"
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
            </div>
        </template>
    </div>
</template>

<style scoped>
.git-panel {
    height: 100%;
    display: flex;
    flex-direction: column;
    position: relative;
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


.git-panel-header {
    & + wa-divider {
        flex-shrink: 0;
        --width: 4px;
        --spacing: 0;
    }
}

/* ----- Content area ----- */

.git-panel-content {
    flex: 1;
    min-height: 0;
    position: relative;
    overflow: hidden;
}

/* ----- Git log overlay (absolute over content) ----- */

.gitlog-overlay {
    position: absolute;
    inset: 0;
    z-index: 1;
    overflow: hidden;
    background: var(--wa-color-surface-default);
}
</style>
