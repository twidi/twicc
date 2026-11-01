# Search Frontend Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a full-text search overlay to TwiCC that queries the existing `/api/search/` endpoint and lets users navigate to matching sessions.

**Architecture:** A single `SearchOverlay.vue` component mounted in `App.vue`, triggered by keyboard shortcut, sidebar button, and Command Palette. State lives in the component (no Pinia store). Uses `apiFetch` + `debounce` for API calls.

**Tech Stack:** Vue 3 (Composition API), Web Awesome components (`wa-dialog`, `wa-input`, `wa-select`, `wa-icon`, `wa-button`), existing `apiFetch` and `debounce` utilities.

**Important:** No commits during implementation. All changes will be committed only when explicitly requested by the user.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `frontend/src/components/SearchOverlay.vue` | Create | Main search overlay component (dialog, input, filters, results, keyboard nav) |
| `frontend/src/App.vue` | Modify | Mount `SearchOverlay`, add `Ctrl+Shift+F` listener |
| `frontend/src/commands/staticCommands.js` | Modify | Add `nav.search` command |
| `frontend/src/views/ProjectView.vue` | Modify | Add "+" button next to filter input |

---

## Task 1: Create `SearchOverlay.vue` — skeleton with dialog open/close

**Files:**
- Create: `frontend/src/components/SearchOverlay.vue`

- [ ] **Step 1: Create the component with dialog, open/close, and event listener**

```vue
<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'

const dialogRef = ref(null)

function open() {
    if (dialogRef.value) {
        dialogRef.value.open = true
    }
}

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
}

function handleOpenSearch() {
    open()
}

onMounted(() => {
    window.addEventListener('twicc:open-search', handleOpenSearch)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:open-search', handleOpenSearch)
})

defineExpose({ open })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        class="search-overlay"
        without-header
        @wa-hide="close"
    >
        <div class="search-container">
            <p>Search overlay placeholder</p>
        </div>
    </wa-dialog>
</template>

<style scoped>
.search-overlay {
    --width: min(720px, calc(100vw - 2rem));
}

.search-overlay::part(panel) {
    margin-top: 10vh;
    margin-bottom: auto;
}

.search-container {
    padding: var(--wa-space-m);
}

@media (width < 640px) {
    .search-overlay {
        --width: 100vw;
    }
    .search-overlay::part(panel) {
        margin-top: 0;
        height: 100vh;
        height: 100dvh;
        max-height: 100vh;
        max-height: 100dvh;
        border-radius: 0;
    }
}
</style>
```

- [ ] **Step 2: Verify the dialog renders and opens**

Mount in `App.vue` (Task 2) to test. For now, just verify the file has no syntax errors by checking the frontend dev server logs.

---

## Task 2: Mount in `App.vue` and add `Ctrl+Shift+F` shortcut

**Files:**
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Import and mount SearchOverlay**

In `<script setup>`, add the import (after the CommandPalette import, line 12):

```javascript
import SearchOverlay from './components/SearchOverlay.vue'
```

Add a ref (after `commandPaletteRef`, line 59):

```javascript
const searchOverlayRef = ref(null)
```

- [ ] **Step 2: Add Ctrl+Shift+F to the global keydown handler**

Modify `handleGlobalKeydown` (line 61) to also handle Ctrl+Shift+F:

```javascript
function handleGlobalKeydown(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        e.stopPropagation()
        commandPaletteRef.value?.open()
    }
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'F') {
        e.preventDefault()
        e.stopPropagation()
        searchOverlayRef.value?.open()
    }
}
```

- [ ] **Step 3: Add the component to the template**

After `<CommandPalette ref="commandPaletteRef" />` (line 106):

```html
<SearchOverlay ref="searchOverlayRef" />
```

- [ ] **Step 4: Test**

Press `Ctrl+Shift+F` in the browser — the placeholder overlay should appear.

---

## Task 3: Add Command Palette entry

**Files:**
- Modify: `frontend/src/commands/staticCommands.js`

- [ ] **Step 1: Add the `nav.search` command**

In the `registerCommands` array, after the `nav.all-projects` command (after line 131), add:

```javascript
{
    id: 'nav.search',
    label: 'Search Sessions\u2026',
    icon: 'magnifying-glass',
    category: 'navigation',
    action: () => window.dispatchEvent(new CustomEvent('twicc:open-search')),
},
```

- [ ] **Step 2: Test**

Open Command Palette (Ctrl+K), type "search" — the "Search Sessions…" command should appear. Clicking it should open the overlay.

---

## Task 4: Add "+" button in sidebar

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`

- [ ] **Step 1: Add the button after the search input**

In the template, after the closing `</wa-input>` tag of the session search (line 790), before the closing `</div>` of the sidebar-header-row (line 791), add:

```html
                    <wa-button
                        id="search-advanced-button"
                        variant="neutral"
                        appearance="filled-outlined"
                        size="small"
                        class="search-advanced-button"
                        @click="openAdvancedSearch"
                    >
                        <wa-icon name="plus"></wa-icon>
                    </wa-button>
                    <AppTooltip for="search-advanced-button">Full-text search (Ctrl+Shift+F)</AppTooltip>
```

- [ ] **Step 2: Add the handler function**

In the `<script setup>`, add the handler (can go near the other handler functions):

```javascript
function openAdvancedSearch() {
    window.dispatchEvent(new CustomEvent('twicc:open-search'))
}
```

- [ ] **Step 3: Add CSS for the button**

In the `<style scoped>` section, add:

```css
.search-advanced-button {
    flex-shrink: 0;
}
```

- [ ] **Step 4: Test**

The "+" button should appear next to the filter input in the sidebar. Clicking it opens the search overlay.

---

## Task 5: Search input with debounced API call

**Files:**
- Modify: `frontend/src/components/SearchOverlay.vue`

- [ ] **Step 1: Add search input, state, and debounced API call**

Replace the entire `SearchOverlay.vue` content with the full implementation including search input, state management, and API integration:

```vue
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
import { useDataStore } from '../stores/data'
import { apiFetch } from '../utils/api'
import { debounce } from '../utils/debounce'

const store = useDataStore()

// ─── Dialog state ──────────────────────────────────────────────────────────
const dialogRef = ref(null)
const searchInputRef = ref(null)
const isOpen = ref(false)

function open() {
    if (dialogRef.value) {
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
    // Prevent default only if we want to keep the dialog open (we don't)
    isOpen.value = false
}

function handleAfterShow() {
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
const filters = reactive({
    projectId: '',
    from: '',
    datePreset: '',
    includeArchived: false,
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

// ─── Projects for filter dropdown ──────────────────────────────────────────
const projects = computed(() =>
    store.getProjects.filter(p => !p.archived)
)

// ─── API call ──────────────────────────────────────────────────────────────
async function performSearch(resetOffset = true) {
    const q = query.value.trim()
    if (q.length < 2) {
        results.value = []
        totalSessions.value = 0
        error.value = null
        return
    }

    if (!searchIndexReady.value) {
        error.value = 'Search index is still being built…'
        return
    }

    if (resetOffset) {
        offset.value = 0
    }

    isLoading.value = true
    error.value = null

    try {
        const params = new URLSearchParams({ q, limit: LIMIT, offset: offset.value })
        if (filters.projectId) params.set('project_id', filters.projectId)
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
async function navigateToResult(result) {
    visitedSessionIds.add(result.session_id)
    close()

    const { useRouter } = await import('vue-router')
    const router = useRouter()

    // Determine route: prefer the "projects-session" route for cross-project search
    router.push({
        name: 'projects-session',
        params: {
            projectId: result.project_id,
            sessionId: result.session_id,
        },
    })
}

// ─── Keyboard navigation ───────────────────────────────────────────────────
const PAGE_SIZE = 5

function handleKeydown(e) {
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
        @wa-hide="handleDialogHide"
        @wa-after-show="handleAfterShow"
    >
        <div class="search-container" @keydown="handleKeydown">
            <!-- Search input -->
            <div class="search-header">
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

                <!-- Filters row -->
                <div class="search-filters">
                    <wa-select
                        v-model="filters.projectId"
                        placeholder="All projects"
                        size="small"
                        with-clear
                        class="filter-select"
                    >
                        <wa-option
                            v-for="p in projects"
                            :key="p.id"
                            :value="p.id"
                        >
                            {{ store.getProjectDisplayName(p.id) }}
                        </wa-option>
                    </wa-select>

                    <wa-select
                        v-model="filters.from"
                        placeholder="Any author"
                        size="small"
                        with-clear
                        class="filter-select"
                    >
                        <wa-option value="user">User</wa-option>
                        <wa-option value="assistant">Assistant</wa-option>
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

            <!-- Index not ready state -->
            <div v-if="!searchIndexReady && query.trim().length >= 2" class="search-index-building">
                <wa-icon name="hourglass-half"></wa-icon>
                <span>Search index is being built… ({{ searchIndexPercent }}%)</span>
                <wa-progress-bar :value="searchIndexPercent"></wa-progress-bar>
            </div>

            <!-- Error state -->
            <div v-else-if="error" class="search-error">
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
                    @mouseenter="selectedIndex = index"
                >
                    <div class="result-header">
                        <span class="result-title">{{ result.session_title || result.session_id }}</span>
                        <div class="result-meta">
                            <span
                                v-if="result.project_name"
                                class="result-project-badge"
                                :style="projectBadgeStyle(result.project_id)"
                            >
                                {{ result.project_name }}
                            </span>
                            <wa-relative-time
                                v-if="result.matches?.[0]?.timestamp"
                                :date="result.matches[0].timestamp"
                                class="result-date"
                            ></wa-relative-time>
                        </div>
                    </div>
                    <div class="result-snippets">
                        <div
                            v-for="(match, mi) in result.matches.slice(0, 3)"
                            :key="mi"
                            class="result-snippet"
                        >
                            <span class="snippet-role">{{ match.from }}</span>
                            <span class="snippet-text" v-html="match.snippet"></span>
                        </div>
                        <div v-if="result.matches.length > 3" class="snippet-more">
                            +{{ result.matches.length - 3 }} more matches
                        </div>
                    </div>
                    <div v-if="visitedSessionIds.has(result.session_id)" class="visited-indicator">
                        ✓
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

            <!-- Footer with keyboard hints -->
            <div v-if="results.length > 0" class="search-footer">
                <span class="keyboard-hints">
                    <kbd>↑</kbd><kbd>↓</kbd> navigate · <kbd>Enter</kbd> open · <kbd>Esc</kbd> close
                </span>
                <span class="results-count">
                    {{ Math.min(results.length, totalSessions) }} of {{ totalSessions }} sessions
                </span>
            </div>
        </div>
    </wa-dialog>
</template>

<style scoped>
.search-overlay {
    --width: min(720px, calc(100vw - 2rem));
}

.search-overlay::part(panel) {
    margin-top: 10vh;
    margin-bottom: auto;
}

.search-overlay::part(body) {
    padding: 0;
}

.search-container {
    display: flex;
    flex-direction: column;
    max-height: calc(80vh - 10vh);
}

.search-header {
    padding: var(--wa-space-m);
    border-bottom: 1px solid var(--wa-color-border-default);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.search-input {
    width: 100%;
}

.search-filters {
    display: flex;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
    align-items: center;
}

.filter-select {
    min-width: 0;
    flex: 1;
    min-width: 8rem;
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

/* Results area */
.search-results {
    overflow-y: auto;
    padding: var(--wa-space-s);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    max-height: 60vh;
}

.search-result-card {
    padding: var(--wa-space-s) var(--wa-space-m);
    border-radius: var(--wa-border-radius-m);
    border-left: 3px solid var(--wa-color-brand-fill-loud);
    background: var(--wa-color-surface-raised);
    cursor: pointer;
    position: relative;
    transition: background-color 0.1s;
}

.search-result-card:hover,
.search-result-card.selected {
    background: var(--wa-color-neutral-fill-quiet);
    outline: 1px solid var(--wa-color-brand-border-loud);
    outline-offset: -1px;
}

.search-result-card.visited {
    opacity: 0.55;
    border-left-color: var(--wa-color-neutral-fill-loud);
}

.result-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: var(--wa-space-s);
    margin-bottom: var(--wa-space-2xs);
}

.result-title {
    font-weight: 600;
    font-size: var(--wa-font-size-m);
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
}

.result-project-badge {
    font-size: var(--wa-font-size-2xs);
    padding: 1px var(--wa-space-xs);
    border-radius: var(--wa-border-radius-s);
    background: var(--badge-bg, var(--wa-color-neutral-fill-quiet));
    color: var(--badge-fg, var(--wa-color-text-quiet));
    white-space: nowrap;
}

.result-date {
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-text-quiet);
}

.result-snippets {
    display: flex;
    flex-direction: column;
    gap: 2px;
}

.result-snippet {
    display: flex;
    gap: var(--wa-space-xs);
    align-items: baseline;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    line-height: 1.5;
}

.snippet-role {
    font-size: var(--wa-font-size-3xs);
    padding: 0 var(--wa-space-2xs);
    background: var(--wa-color-neutral-fill-quiet);
    border-radius: var(--wa-border-radius-s);
    color: var(--wa-color-text-quiet);
    flex-shrink: 0;
}

.snippet-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.snippet-text :deep(b) {
    color: var(--wa-color-warning-text);
    font-weight: 700;
}

.snippet-more {
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-text-quiet);
    padding-left: var(--wa-space-l);
}

.visited-indicator {
    position: absolute;
    top: var(--wa-space-2xs);
    right: var(--wa-space-xs);
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-text-quiet);
}

/* States */
.search-loading,
.search-empty,
.search-hint,
.search-index-building {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-xl) var(--wa-space-m);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.search-index-building {
    padding: var(--wa-space-l) var(--wa-space-xl);
}

.search-index-building wa-progress-bar {
    width: 100%;
    max-width: 300px;
}

.search-error {
    padding: var(--wa-space-m);
}

.search-load-more {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s);
}

/* Footer */
.search-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: var(--wa-space-xs) var(--wa-space-m);
    border-top: 1px solid var(--wa-color-border-default);
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-text-quiet);
}

.keyboard-hints {
    display: flex;
    gap: var(--wa-space-2xs);
    align-items: center;
}

.keyboard-hints kbd {
    background: var(--wa-color-neutral-fill-quiet);
    padding: 0 var(--wa-space-2xs);
    border-radius: var(--wa-border-radius-s);
    font-family: inherit;
    font-size: var(--wa-font-size-3xs);
}

/* Mobile: full-screen */
@media (width < 640px) {
    .search-overlay {
        --width: 100vw;
    }
    .search-overlay::part(panel) {
        margin-top: 0;
        height: 100vh;
        height: 100dvh;
        max-height: 100vh;
        max-height: 100dvh;
        border-radius: 0;
    }
    .search-container {
        max-height: 100vh;
        max-height: 100dvh;
    }
    .search-results {
        max-height: none;
        flex: 1;
    }
    .keyboard-hints {
        display: none;
    }
    .search-filters {
        flex-direction: column;
    }
    .filter-select {
        width: 100%;
    }
}
</style>
```

- [ ] **Step 2: Add project badge color helper**

In the `<script setup>` section, add the helper function for project badge colors (before `defineExpose`):

```javascript
function projectBadgeStyle(projectId) {
    const project = store.projects[projectId]
    if (!project?.color) return {}
    return {
        '--badge-bg': `${project.color}20`,
        '--badge-fg': project.color,
    }
}
```

Note: This function is already referenced in the template via `:style="projectBadgeStyle(result.project_id)"`.

---

## Task 6: Fix router navigation for search results

**Files:**
- Modify: `frontend/src/components/SearchOverlay.vue`

- [ ] **Step 1: Fix the `navigateToResult` function to use lazy router import**

The initial implementation uses `import('vue-router')` which won't work correctly — `useRouter()` must be called from setup context. Instead, we need to get the router during setup and use it in the function. But per CLAUDE.md, we must avoid importing router.js directly from components that could create circular imports.

Since `SearchOverlay.vue` is mounted in `App.vue` which already imports the router, we can safely use `useRouter()` at the top-level of `<script setup>`:

Add at the top of `<script setup>` (after the other imports):

```javascript
import { useRouter } from 'vue-router'
const router = useRouter()
```

Then simplify `navigateToResult`:

```javascript
async function navigateToResult(result) {
    visitedSessionIds.add(result.session_id)
    close()

    router.push({
        name: 'projects-session',
        params: {
            projectId: result.project_id,
            sessionId: result.session_id,
        },
    })
}
```

Remove the `async` keyword since we no longer need the dynamic import.

- [ ] **Step 2: Test end-to-end**

1. Press Ctrl+Shift+F
2. Type a search query that should match (e.g., a word you know appears in your sessions)
3. Verify results appear with session titles, project badges, and snippets
4. Click a result — verify it navigates to the session
5. Press Ctrl+Shift+F again — verify previous results are still there
6. Verify the clicked result is dimmed (visited indicator)

---

## Task 7: Final polish and edge cases

**Files:**
- Modify: `frontend/src/components/SearchOverlay.vue`

- [ ] **Step 1: Handle the case where search overlay is opened while already open**

In the `open()` function, if the dialog is already open, just re-focus the input:

```javascript
function open() {
    if (dialogRef.value) {
        if (dialogRef.value.open) {
            // Already open — just re-focus
            searchInputRef.value?.focus()
            return
        }
        dialogRef.value.open = true
        isOpen.value = true
    }
}
```

- [ ] **Step 2: Ensure Ctrl+Shift+F doesn't conflict when overlay is open**

The overlay's keydown handler should not intercept Ctrl+Shift+F. In `handleKeydown`, add at the top:

```javascript
// Don't intercept modifier key combos (let App.vue handle Ctrl+Shift+F toggle)
if (e.ctrlKey || e.metaKey) return
```

- [ ] **Step 3: Test the complete feature**

Verify all entry points:
1. Ctrl+Shift+F opens the overlay
2. "+" button in sidebar opens the overlay
3. Command Palette → "Search Sessions…" opens the overlay
4. Search works with filters (project, author, date, archived)
5. Keyboard navigation works (↑↓ Enter Esc Home End PageUp/Down)
6. Results are preserved when closing and reopening
7. Visited results are marked
8. "Load more" works for pagination
9. Mobile: overlay is full-screen
10. "Index not ready" state shows progress bar when index is building
