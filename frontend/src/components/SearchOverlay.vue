<script setup>
/**
 * SearchOverlay — Full-text search dialog with filters, results, and keyboard navigation.
 *
 * Opens via:
 * - Ctrl+Shift+F (global shortcut in App.vue)
 * - "+" button in sidebar (ProjectView.vue)
 * - "Search Sessions…" command in Command Palette
 *
 * All three dispatch CustomEvent('twicc:open-search'), which this component listens for.
 * State (query, results, filters, visited) is preserved when the dialog is closed and reopened.
 */

import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { useWorkspacesStore } from '../stores/workspaces'
import { apiFetch } from '../utils/api'
import { debounce } from '../utils/debounce'
import { formatDate } from '../utils/date'
import { pendingSessionSearch } from '../utils/pendingSearch'
import { SESSION_TIME_FORMAT } from '../constants'
import ProjectBadge from './ProjectBadge.vue'
import ProjectSelectOptions from './ProjectSelectOptions.vue'
import AppTooltip from './AppTooltip.vue'

const route = useRoute()
const router = useRouter()
const store = useDataStore()
const settingsStore = useSettingsStore()
const workspacesStore = useWorkspacesStore()

// ─── Time display (follows the same setting as session list) ─────────────
const sessionTimeFormat = computed(() => settingsStore.getSessionTimeFormat)
const useRelativeTime = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ||
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_NARROW
)
const relativeTimeFormat = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow'
)

/** Convert an ISO timestamp string to a Date object (for wa-relative-time) */
function isoToDate(isoStr) {
    return isoStr ? new Date(isoStr) : null
}

/** Convert an ISO timestamp string to Unix seconds (for formatDate) */
function isoToUnix(isoStr) {
    if (!isoStr) return null
    return new Date(isoStr).getTime() / 1000
}

// ─── Dialog state ──────────────────────────────────────────────────────────
const dialogRef = ref(null)
const searchInputRef = ref(null)
const isOpen = ref(false)

function open() {
    if (dialogRef.value) {
        if (dialogRef.value.open) {
            // Already open — just re-focus
            searchInputRef.value?.focus()
            return
        }

        // Pre-select filter based on current route context:
        // workspace active → workspace filter, single-project → project filter, else → all
        const wsId = route.query.workspace
        const isAllProjectsMode = route.name?.startsWith('projects-') || !route.params.projectId
        if (wsId) {
            filters.projectId = 'workspace:' + wsId
        } else {
            filters.projectId = isAllProjectsMode ? '' : (route.params.projectId || '')
        }

        dialogRef.value.open = true
        isOpen.value = true
    }
}

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
        isOpen.value = false
    }
}

function handleDialogHide(e) {
    // Only handle hide events from the dialog itself, not from child components
    // (wa-select fires wa-hide when its dropdown closes, which bubbles up)
    if (e.target !== dialogRef.value) return
    isOpen.value = false
}

function handleAfterShow(e) {
    // Only react to the dialog's own after-show, not child components
    // (wa-select fires wa-after-show when its dropdown opens, and it bubbles up)
    if (e.target !== dialogRef.value) return

    // Focus the search input after dialog animation completes
    nextTick(() => {
        const input = searchInputRef.value
        if (input) {
            input.focus()
            // If there's existing text, select it for easy replacement
            const len = input.value?.length || 0
            if (len > 0) {
                input.select()
            }
        }
    })
}

// ─── Search state (preserved across open/close) ───────────────────────────
const query = ref('')
const results = ref([])
const totalSessions = ref(0)
const offset = ref(0)
const isLoading = ref(false)
const error = ref(null)
const selectedIndex = ref(0)
const visitedSessionIds = reactive(new Set())

const LIMIT = 20

// ─── Filters ───────────────────────────────────────────────────────────────
const filtersExpanded = ref(false)
const filters = reactive({
    projectId: '',
    from: '',
    datePreset: '',
    includeArchived: false,
})

/** Number of active filters (for the badge on the mobile toggle button) */
const activeFilterCount = computed(() => {
    let count = 0
    if (filters.projectId) count++
    if (filters.from) count++
    if (filters.datePreset) count++
    if (filters.includeArchived) count++
    return count
})

// Compute date range from preset
const dateRange = computed(() => {
    const now = new Date()
    switch (filters.datePreset) {
        case '7d': {
            const d = new Date(now)
            d.setDate(d.getDate() - 7)
            return { after: d.toISOString() }
        }
        case '30d': {
            const d = new Date(now)
            d.setDate(d.getDate() - 30)
            return { after: d.toISOString() }
        }
        case '3m': {
            const d = new Date(now)
            d.setMonth(d.getMonth() - 3)
            return { after: d.toISOString() }
        }
        default:
            return {}
    }
})

// ─── Search index readiness ────────────────────────────────────────────────
const searchIndexReady = computed(() => {
    const progress = store.searchIndexProgress
    return progress?.completed === true
})

const searchIndexPercent = computed(() => {
    const p = store.searchIndexProgress
    if (!p) return 0
    if (p.completed) return 100
    if (p.total === 0) return 0
    return Math.round((p.current / p.total) * 100)
})

// ─── Projects and workspaces for filter dropdown ─────────────────────────
const projects = computed(() =>
    store.getProjects.filter(p => !p.archived)
)

/** Active workspace project IDs (ordered), or null when no workspace is active. */
const activeWsProjectIds = computed(() => {
    const wsId = route.query.workspace
    return wsId ? workspacesStore.getVisibleProjectIds(wsId) : null
})

const activeWsLabel = computed(() => {
    const wsId = route.query.workspace
    if (!wsId) return null
    const ws = workspacesStore.getWorkspaceById(wsId)
    return ws ? `${ws.name} projects` : null
})

/** Selectable workspaces for the filter dropdown */
const selectableWorkspaces = computed(() => workspacesStore.getSelectableWorkspaces)

/** Whether the current filter is a workspace */
const isWorkspaceFilter = computed(() => filters.projectId.startsWith('workspace:'))

/** Workspace ID from the filter (if workspace is selected) */
const filterWorkspaceId = computed(() =>
    isWorkspaceFilter.value ? filters.projectId.slice('workspace:'.length) : null
)

/** Project IDs for the currently selected workspace filter */
const filterWorkspaceProjectIds = computed(() =>
    filterWorkspaceId.value ? workspacesStore.getVisibleProjectIds(filterWorkspaceId.value) : null
)

// Selected project/workspace visual indicators for the closed select
const selectedProjectColor = computed(() => {
    if (!filters.projectId || isWorkspaceFilter.value) return null
    const project = store.getProject(filters.projectId)
    return project?.color || null
})

const selectedWorkspaceColor = computed(() => {
    if (!isWorkspaceFilter.value) return null
    const ws = workspacesStore.getWorkspaceById(filterWorkspaceId.value)
    return ws?.color || null
})

// ─── API call ──────────────────────────────────────────────────────────────
async function performSearch(resetOffset = true) {
    const q = query.value.trim()
    if (q.length < 2) {
        results.value = []
        totalSessions.value = 0
        error.value = null
        return
    }

    if (resetOffset) {
        offset.value = 0
    }

    isLoading.value = true
    error.value = null

    try {
        const params = new URLSearchParams({ q, limit: LIMIT, offset: offset.value })
        if (isWorkspaceFilter.value && filterWorkspaceProjectIds.value) {
            for (const pid of filterWorkspaceProjectIds.value) {
                params.append('project_ids', pid)
            }
        } else if (filters.projectId) {
            params.set('project_id', filters.projectId)
        }
        if (filters.from) params.set('from', filters.from)
        if (filters.includeArchived) params.set('include_archived', 'true')
        if (dateRange.value.after) params.set('after', dateRange.value.after)

        const res = await apiFetch(`/api/search/?${params}`)

        if (!res.ok) {
            if (res.status === 503) {
                error.value = 'Search index is still being built…'
            } else if (res.status === 400) {
                const data = await res.json()
                error.value = data.error || 'Invalid search query'
            } else {
                error.value = 'Search error, please try again'
            }
            return
        }

        const data = await res.json()

        if (resetOffset) {
            results.value = data.results || []
        } else {
            // Append for "load more"
            results.value = [...results.value, ...(data.results || [])]
        }
        totalSessions.value = data.total_sessions || 0
        selectedIndex.value = 0
    } catch (e) {
        error.value = 'Network error, please try again'
    } finally {
        isLoading.value = false
    }
}

const debouncedSearch = debounce(() => performSearch(true), 300)

// Trigger search when query or filters change
watch(query, (newVal) => {
    if (newVal.trim().length === 0) {
        // Reset state when query is cleared
        results.value = []
        totalSessions.value = 0
        offset.value = 0
        error.value = null
        visitedSessionIds.clear()
        debouncedSearch.cancel()
    } else {
        debouncedSearch()
    }
})

watch(filters, () => {
    if (query.value.trim().length >= 2) {
        debouncedSearch()
    }
})

// ─── Load more ─────────────────────────────────────────────────────────────
const hasMore = computed(() => results.value.length < totalSessions.value)

function loadMore() {
    offset.value = results.value.length
    performSearch(false)
}

// ─── Navigation ────────────────────────────────────────────────────────────

// Temporarily holds the pending search data until the dialog close animation
// completes (wa-after-hide). Setting pendingSessionSearch only after the dialog
// is fully closed prevents the dialog's focus restoration from stealing focus
// from the in-session search input.
let deferredPendingSearch = null

function navigateToResult(result) {
    visitedSessionIds.add(result.session_id)

    // Defer the pending search until the dialog close animation completes
    const q = query.value.trim()
    if (q.length >= 2) {
        deferredPendingSearch = { sessionId: result.session_id, query: q }
    }

    close()

    router.push({
        name: 'projects-session',
        params: {
            projectId: result.project_id,
            sessionId: result.session_id,
        },
    })
}

function handleAfterHide(e) {
    if (e.target !== dialogRef.value) return
    if (deferredPendingSearch) {
        pendingSessionSearch.value = deferredPendingSearch
        deferredPendingSearch = null
    }
}

// ─── Keyboard navigation ───────────────────────────────────────────────────
const PAGE_SIZE = 5

function handleKeydown(e) {
    // Don't intercept modifier key combos (let App.vue handle Ctrl+Shift+F toggle)
    if (e.ctrlKey || e.metaKey) return

    if (!results.value.length) return

    switch (e.key) {
        case 'ArrowDown':
            e.preventDefault()
            selectedIndex.value = Math.min(selectedIndex.value + 1, results.value.length - 1)
            scrollSelectedIntoView()
            break
        case 'ArrowUp':
            e.preventDefault()
            selectedIndex.value = Math.max(selectedIndex.value - 1, 0)
            scrollSelectedIntoView()
            break
        case 'Enter':
            e.preventDefault()
            if (results.value[selectedIndex.value]) {
                navigateToResult(results.value[selectedIndex.value])
            }
            break
        case 'Home':
            e.preventDefault()
            selectedIndex.value = 0
            scrollSelectedIntoView()
            break
        case 'End':
            e.preventDefault()
            selectedIndex.value = results.value.length - 1
            scrollSelectedIntoView()
            break
        case 'PageDown':
            e.preventDefault()
            selectedIndex.value = Math.min(selectedIndex.value + PAGE_SIZE, results.value.length - 1)
            scrollSelectedIntoView()
            break
        case 'PageUp':
            e.preventDefault()
            selectedIndex.value = Math.max(selectedIndex.value - PAGE_SIZE, 0)
            scrollSelectedIntoView()
            break
    }
}

function scrollSelectedIntoView() {
    nextTick(() => {
        const el = document.querySelector('.search-result-card.selected')
        if (el) {
            el.scrollIntoView({ block: 'nearest' })
        }
    })
}

// ─── Event listeners ───────────────────────────────────────────────────────
function handleOpenSearch() {
    open()
}

onMounted(() => {
    window.addEventListener('twicc:open-search', handleOpenSearch)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:open-search', handleOpenSearch)
    debouncedSearch.cancel()
})

defineExpose({ open })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        class="search-overlay"
        without-header
        light-dismiss
        @wa-hide="handleDialogHide"
        @wa-after-show="handleAfterShow"
        @wa-after-hide="handleAfterHide"
    >
        <div class="search-container" @keydown="handleKeydown">
            <!-- Search input -->
            <div class="search-header">
                <div class="search-input-row">
                    <wa-input
                        ref="searchInputRef"
                        v-model="query"
                        placeholder="Search in all sessions..."
                        size="medium"
                        with-clear
                        class="search-input"
                    >
                        <wa-icon slot="start" name="magnifying-glass"></wa-icon>
                    </wa-input>
                    <wa-button
                        class="filters-toggle"
                        variant="neutral"
                        :appearance="filtersExpanded ? 'outlined' : 'plain'"
                        size="small"
                        @click="filtersExpanded = !filtersExpanded"
                    >
                        <wa-icon name="sliders"></wa-icon>
                        <wa-badge v-if="activeFilterCount > 0" variant="brand" pill>{{ activeFilterCount }}</wa-badge>
                    </wa-button>
                </div>

                <!-- Filters row -->
                <div class="search-filters" :class="{ 'filters-expanded': filtersExpanded }">
                    <wa-select
                        v-model="filters.projectId"
                        placeholder="All projects"
                        size="small"
                        with-clear
                        class="filter-select"
                    >
                        <span
                            v-if="filters.projectId && !isWorkspaceFilter"
                            slot="start"
                            class="selected-project-dot"
                            :style="selectedProjectColor ? { '--dot-color': selectedProjectColor } : null"
                        ></span>
                        <wa-icon
                            v-if="isWorkspaceFilter"
                            slot="start"
                            name="layer-group"
                            :style="selectedWorkspaceColor ? { color: selectedWorkspaceColor } : null"
                        ></wa-icon>
                        <!-- Workspaces -->
                        <template v-if="selectableWorkspaces.length">
                            <wa-option disabled class="section-header-option">Workspaces</wa-option>
                            <wa-option
                                v-for="ws in selectableWorkspaces"
                                :key="ws.id"
                                :value="'workspace:' + ws.id"
                                :label="ws.name"
                            >
                                <span class="workspace-option">
                                    <wa-icon name="layer-group" auto-width :style="ws.color ? { color: ws.color } : null"></wa-icon>
                                    {{ ws.name }}
                                </span>
                            </wa-option>
                            <wa-divider></wa-divider>
                            <wa-option v-if="!activeWsProjectIds" disabled class="section-header-option">Projects</wa-option>
                        </template>
                        <!-- Projects -->
                        <ProjectSelectOptions :projects="projects" :priority-project-ids="activeWsProjectIds" :priority-label="activeWsLabel" />
                    </wa-select>

                    <wa-select
                        v-model="filters.from"
                        placeholder="Any source"
                        size="small"
                        with-clear
                        class="filter-select"
                    >
                        <wa-option value="user">User</wa-option>
                        <wa-option value="assistant">Assistant</wa-option>
                        <wa-option value="title">Title</wa-option>
                    </wa-select>

                    <wa-select
                        v-model="filters.datePreset"
                        placeholder="Any date"
                        size="small"
                        with-clear
                        class="filter-select"
                    >
                        <wa-option value="7d">Last 7 days</wa-option>
                        <wa-option value="30d">Last 30 days</wa-option>
                        <wa-option value="3m">Last 3 months</wa-option>
                    </wa-select>

                    <label class="filter-checkbox">
                        <input type="checkbox" v-model="filters.includeArchived" />
                        <span>Archived</span>
                    </label>
                </div>
            </div>

            <!-- Indexing in progress banner (non-blocking — shown alongside results) -->
            <div v-if="!searchIndexReady" class="search-index-banner">
                <wa-icon name="hourglass-half"></wa-icon>
                <span>Indexing in progress ({{ searchIndexPercent }}%) — results may be incomplete</span>
                <wa-progress-bar :value="searchIndexPercent"></wa-progress-bar>
            </div>

            <!-- Error state -->
            <div v-if="error" class="search-error">
                <wa-callout variant="danger">{{ error }}</wa-callout>
            </div>

            <!-- Results list -->
            <div v-else-if="results.length > 0" class="search-results">
                <div
                    v-for="(result, index) in results"
                    :key="result.session_id"
                    class="search-result-card"
                    :class="{
                        selected: index === selectedIndex,
                        visited: visitedSessionIds.has(result.session_id),
                    }"
                    @click="navigateToResult(result)"
                    @pointerenter="selectedIndex = index"
                >
                    <div class="result-header">
                        <wa-tag v-if="result.archived" size="small" variant="neutral" class="archived-tag">Arch.</wa-tag>
                        <span class="result-title">{{ result.session_title || result.session_id }}</span>
                        <div class="result-meta">
                            <ProjectBadge
                                v-if="result.project_id && (!filters.projectId || isWorkspaceFilter)"
                                :id="`search-project-${result.session_id}`"
                                :projectId="result.project_id"
                                class="result-project"
                            />
                            <AppTooltip v-if="result.project_id && (!filters.projectId || isWorkspaceFilter)" :for="`search-project-${result.session_id}`">{{ store.getProject(result.project_id)?.directory || result.project_id }}</AppTooltip>
                            <span v-if="result.matches?.[0]?.timestamp" :id="`search-date-${result.session_id}`" class="result-date">
                                <wa-relative-time v-if="useRelativeTime" :date.prop="isoToDate(result.matches[0].timestamp)" :format="relativeTimeFormat" numeric="always" sync></wa-relative-time>
                                <template v-else>{{ formatDate(isoToUnix(result.matches[0].timestamp), { smart: true }) }}</template>
                            </span>
                            <AppTooltip v-if="result.matches?.[0]?.timestamp" :for="`search-date-${result.session_id}`">{{ formatDate(isoToUnix(result.matches[0].timestamp)) }}</AppTooltip>
                        </div>
                    </div>
                    <div class="result-snippets">
                        <div
                            v-for="(match, mi) in result.matches.slice(0, 3)"
                            :key="mi"
                            class="result-snippet"
                        >
                            <span :id="`search-role-${result.session_id}-${mi}`" class="snippet-role">{{ match.from?.[0]?.toUpperCase() }}</span>
                            <AppTooltip :for="`search-role-${result.session_id}-${mi}`">{{ match.from }}</AppTooltip>
                            <span class="snippet-text" v-html="match.snippet"></span>
                        </div>
                        <div v-if="result.matches.length > 3" class="snippet-more">
                            +{{ result.matches.length - 3 }} more matches
                        </div>
                    </div>
                </div>

                <!-- Load more -->
                <div v-if="hasMore" class="search-load-more">
                    <wa-button
                        variant="neutral"
                        appearance="outlined"
                        size="small"
                        :disabled="isLoading"
                        @click="loadMore"
                    >
                        <wa-spinner v-if="isLoading" slot="start"></wa-spinner>
                        Load more results
                    </wa-button>
                </div>
            </div>

            <!-- Loading state (initial search) -->
            <div v-else-if="isLoading" class="search-loading">
                <wa-spinner></wa-spinner>
                <span>Searching…</span>
            </div>

            <!-- Empty state -->
            <div v-else-if="query.trim().length >= 2 && !isLoading" class="search-empty">
                No results found
            </div>

            <!-- Initial state (no query) -->
            <div v-else-if="query.trim().length < 2 && query.trim().length > 0" class="search-hint">
                Type at least 2 characters to search
            </div>

        </div>
    </wa-dialog>
</template>

<style scoped>
/* ─── Dialog chrome — matches CommandPalette styling ────────────────────── */
.search-overlay {
    --width: min(60rem, calc(100vw - 1rem));
    overflow: hidden;
}

.search-overlay::part(body) {
    background: var(--wa-color-surface-default);
    padding: 0;
}

.search-overlay::part(overlay) {
    background: rgba(0, 0, 0, 0.4);
}

/* ─── Layout ────────────────────────────────────────────────────────────── */
.search-container {
    display: flex;
    flex-direction: column;
    max-height: 70dvh;
}

.search-header {
    padding: var(--wa-space-s) var(--wa-space-m);
    border-bottom: 1px solid var(--wa-color-surface-border);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.search-input-row {
    display: flex;
    gap: var(--wa-space-xs);
    align-items: stretch;
}

.search-input {
    flex: 1;
    min-width: 0;
}

.filters-toggle {
    display: none;
    flex-shrink: 0;
    position: relative;
    &::part(base) {
        height: auto;
    }
}

.filters-toggle wa-badge {
    position: absolute;
    top: 0;
    right: 0;
    font-size: var(--wa-font-size-xs);
    border-radius: 100%;
}

.selected-project-dot {
    width: 0.75em;
    height: 0.75em;
    border-radius: 50%;
    flex-shrink: 0;
    border: 1px solid;
    box-sizing: border-box;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-border-quiet));
}

.workspace-option {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.section-header-option {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--wa-color-text-quiet);
}

.filter-select wa-divider {
    --width: 4px;
    --spacing: 4px;
}

/* ─── Filters ────────────────────────────────────────────────────────────── */
.search-filters {
    display: flex;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
    align-items: center;
}

.filter-select {
    flex: 1;
    min-width: 8rem;
    &::part(listbox) {
        overflow: visible;
        overflow-y: auto;
        max-height: 50dvh;
        width: max-content;
        max-width: calc(100vw - 4rem);
        min-width: 100%;
    }
}

.filter-checkbox {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    cursor: pointer;
    white-space: nowrap;
    padding: 0 var(--wa-space-xs);
}

.filter-checkbox input {
    cursor: pointer;
}

/* ─── Results list ──────────────────────────────────────────────────────── */
.search-results {
    overflow-y: auto;
    padding: var(--wa-space-xs) 0;
    max-height: 60dvh;
}

/* ─── Result card — matches CommandPalette .command-item pattern ─────────── */
.search-result-card {
    padding: var(--wa-space-xs) var(--wa-space-m);
    border-radius: var(--wa-border-radius-s);
    margin: 1px var(--wa-space-xs);
    cursor: pointer;
    position: relative;
    user-select: none;
}

.search-result-card.selected {
    background: var(--wa-color-surface-lowered);
}

.search-result-card.visited {
    opacity: 0.5;
}

/* ─── Result card internals ─────────────────────────────────────────────── */
.result-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: var(--wa-space-s);
    margin-bottom: var(--wa-space-2xs);
}

.result-title {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
}

.result-meta {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
    font-size: var(--wa-font-size-s);
}

.result-project {
}

.result-date {
    color: var(--wa-color-text-quiet);
}

/* ─── Snippets ──────────────────────────────────────────────────────────── */
.result-snippets {
    display: flex;
    flex-direction: column;
    gap: 2px;
}

.result-snippet {
    display: flex;
    gap: var(--wa-space-xs);
    align-items: baseline;
    color: var(--wa-color-text-quiet);
    line-height: 1.5;
}

.snippet-role {
    font-size: var(--wa-font-size-3xs);
    padding: 0 var(--wa-space-2xs);
    background: var(--wa-color-neutral-fill-quiet);
    border-radius: var(--wa-border-radius-s);
    color: var(--wa-color-text-muted);
    flex-shrink: 0;
    font-weight: 700;
}

.snippet-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.snippet-text :deep(b) {
    color: var(--wa-color-success-60);
    font-weight: 700;
}

.snippet-more {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-muted);
    padding-left: var(--wa-space-l);
}

.archived-tag {
    flex-shrink: 0;
    line-height: unset;
    height: unset;
}

/* ─── State panels ──────────────────────────────────────────────────────── */
.search-loading,
.search-empty,
.search-hint {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-xl) var(--wa-space-m);
    color: var(--wa-color-text-muted);
    font-size: var(--wa-font-size-s);
}

.search-index-banner {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-xs) var(--wa-space-m);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-muted);
    border-bottom: 1px solid var(--wa-color-surface-border);
}

.search-index-banner wa-progress-bar {
    flex: 1;
    min-width: 60px;
    max-width: 120px;
}

.search-error {
    padding: var(--wa-space-m);
}

.search-load-more {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s);
}

/* ─── Mobile: full-screen ───────────────────────────────────────────────── */
@media (width < 640px) {
    .search-overlay::part(panel) {
        --width: 100vw;
        margin-top: 0;
        height: 90dvh;
        max-height: 90dvh;
        border-radius: 0;
    }
    .search-container {
        max-height: 90dvh;
    }
    .search-results {
        max-height: none;
        flex: 1;
    }
    .filters-toggle {
        display: inline-flex;
    }
    .search-filters {
        display: none;
        flex-direction: column;
    }
    .search-filters.filters-expanded {
        display: flex;
    }
    .filter-select {
        width: 100%;
    }
}
</style>
