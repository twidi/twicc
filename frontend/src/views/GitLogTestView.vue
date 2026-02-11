<script setup>
import { ref, computed } from 'vue'
import {
    GitLog,
    GitLogGraphHTMLGrid,
    GitLogTable,
    GitLogTags,
} from '../components/GitLog'
import { DEFAULT_NODE_SIZE, DEFAULT_ROW_HEIGHT, NODE_BORDER_WIDTH } from '../components/GitLog/constants'
import { fakeEntries, fakeIndexStatus } from '../components/GitLog/__tests__/fakeData'

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------

const isDark = ref(false)
const themeMode = computed(() => (isDark.value ? 'dark' : 'light'))

// ---------------------------------------------------------------------------
// Colour palette
// ---------------------------------------------------------------------------

const PALETTE_OPTIONS = [
    { label: 'Rainbow Light', value: 'rainbow-light' },
    { label: 'Rainbow Dark', value: 'rainbow-dark' },
    { label: 'Neon Aurora Light', value: 'neon-aurora-light' },
    { label: 'Neon Aurora Dark', value: 'neon-aurora-dark' },
]

const selectedPalette = ref('neon-aurora-dark')

// ---------------------------------------------------------------------------
// Sizing
// ---------------------------------------------------------------------------

const nodeSize = ref(DEFAULT_NODE_SIZE)
const rowHeight = ref(DEFAULT_ROW_HEIGHT)
const graphColumnWidth = ref(DEFAULT_NODE_SIZE + NODE_BORDER_WIDTH * 2)

// ---------------------------------------------------------------------------
// Break point theme
// ---------------------------------------------------------------------------

const BREAK_POINT_THEME_OPTIONS = [
    { label: 'Dot', value: 'dot' },
    { label: 'Slash', value: 'slash' },
    { label: 'Arrow', value: 'arrow' },
    { label: 'Ring', value: 'ring' },
    { label: 'Line', value: 'line' },
    { label: 'Double line', value: 'double-line' },
    { label: 'Zig-zag', value: 'zig-zag' },
]

const selectedBreakPointTheme = ref('dot')

// ---------------------------------------------------------------------------
// Orientation
// ---------------------------------------------------------------------------

const isFlipped = ref(false)
const orientation = computed(() => (isFlipped.value ? 'flipped' : 'normal'))

// ---------------------------------------------------------------------------
// Filter
// ---------------------------------------------------------------------------

const filterText = ref('')

const commitFilter = computed(() => {
    const text = filterText.value.trim().toLowerCase()
    if (!text) return undefined
    return (commits) => commits.filter((c) =>
        c.message.toLowerCase().includes(text)
    )
})

// ---------------------------------------------------------------------------
// Pagination
// ---------------------------------------------------------------------------

const pageSize = ref(20)
const currentPage = ref(0)

const paging = computed(() => ({
    size: pageSize.value,
    page: currentPage.value,
}))

const totalPages = computed(() =>
    Math.max(1, Math.ceil(fakeEntries.length / pageSize.value))
)

function goToPage(page) {
    const clamped = Math.max(0, Math.min(page, totalPages.value - 1))
    currentPage.value = clamped
}

// ---------------------------------------------------------------------------
// Commit selection & preview
// ---------------------------------------------------------------------------

const selectedCommit = ref(null)
const previewedCommit = ref(null)

function handleSelectCommit(commit) {
    selectedCommit.value = commit ?? null
}

function handlePreviewCommit(commit) {
    previewedCommit.value = commit ?? null
}

// ---------------------------------------------------------------------------
// Display commit details
// ---------------------------------------------------------------------------

const displayedCommit = computed(() =>
    selectedCommit.value ?? previewedCommit.value
)

const displayedCommitLabel = computed(() => {
    if (selectedCommit.value) return 'Selected'
    if (previewedCommit.value) return 'Previewed (hover)'
    return null
})
</script>

<template>
    <div class="gitlog-test-view" :class="{ dark: isDark }">
        <!-- Toolbar -->
        <header class="toolbar">
            <span class="toolbar-title">GitLog Test</span>
            <input
                v-model="filterText"
                type="text"
                class="control-input"
                placeholder="Filter commits..."
            />
            <select v-model="selectedPalette" class="control-select control-select--palette">
                <option
                    v-for="opt in PALETTE_OPTIONS"
                    :key="opt.value"
                    :value="opt.value"
                >
                    {{ opt.label }}
                </option>
            </select>
            <div class="sizing-group">
                <label class="toggle">
                    <span>Node</span>
                    <input
                        v-model.number="nodeSize"
                        type="number"
                        class="control-input control-input--narrow"
                        min="10"
                        max="40"
                    />
                </label>
                <label class="toggle">
                    <span>Row H</span>
                    <input
                        v-model.number="rowHeight"
                        type="number"
                        class="control-input control-input--narrow"
                        min="20"
                        max="80"
                    />
                </label>
                <label class="toggle">
                    <span>Col W</span>
                    <input
                        v-model.number="graphColumnWidth"
                        type="number"
                        class="control-input control-input--narrow"
                        min="16"
                        max="60"
                    />
                </label>
                <select v-model="selectedBreakPointTheme" class="control-select">
                    <option
                        v-for="opt in BREAK_POINT_THEME_OPTIONS"
                        :key="opt.value"
                        :value="opt.value"
                    >
                        {{ opt.label }}
                    </option>
                </select>
            </div>
            <select v-model.number="pageSize" class="control-select control-select--narrow" @change="currentPage = 0">
                <option :value="10">10</option>
                <option :value="20">20</option>
                <option :value="50">50</option>
            </select>
            <button
                class="control-btn"
                :disabled="currentPage === 0"
                @click="goToPage(currentPage - 1)"
            >&laquo;</button>
            <span class="page-indicator">{{ currentPage + 1 }}/{{ totalPages }}</span>
            <button
                class="control-btn"
                :disabled="currentPage >= totalPages - 1"
                @click="goToPage(currentPage + 1)"
            >&raquo;</button>
            <label class="toggle">
                <input type="checkbox" v-model="isDark" />
                <span>Dark</span>
            </label>
            <label class="toggle">
                <input type="checkbox" v-model="isFlipped" />
                <span>Flipped</span>
            </label>
        </header>

        <!-- GitLog component -->
        <div class="gitlog-container">
            <GitLog
                :entries="fakeEntries"
                current-branch="main"
                :theme="themeMode"
                :colours="selectedPalette"
                :paging="paging"
                :filter="commitFilter"
                :show-git-index="true"
                :index-status="fakeIndexStatus"
                :on-select-commit="handleSelectCommit"
                :on-preview-commit="handlePreviewCommit"
                :show-headers="true"
                :node-size="nodeSize"
                :row-height="rowHeight"
                :graph-column-width="graphColumnWidth"
                :enable-selected-commit-styling="true"
                :enable-previewed-commit-styling="true"
            >
                <template #tags>
                    <GitLogTags />
                </template>
                <template #graph>
                    <GitLogGraphHTMLGrid
                        :orientation="orientation"
                        :break-point-theme="selectedBreakPointTheme"
                        :show-commit-node-tooltips="true"
                    />
                </template>
                <template #table>
                    <GitLogTable timestamp-format="YYYY-MM-DD HH:mm" />
                </template>
            </GitLog>
        </div>

        <!-- Commit detail panel -->
        <div v-if="displayedCommit" class="commit-detail-panel" :class="{ dark: isDark }">
            <div class="commit-detail-header">
                <span class="commit-detail-badge">{{ displayedCommitLabel }}</span>
                <code class="commit-detail-hash">{{ displayedCommit.hash }}</code>
            </div>
            <div class="commit-detail-body">
                <div class="commit-detail-row">
                    <span class="commit-detail-key">Message</span>
                    <span class="commit-detail-value">{{ displayedCommit.message }}</span>
                </div>
                <div class="commit-detail-row">
                    <span class="commit-detail-key">Branch</span>
                    <span class="commit-detail-value">{{ displayedCommit.branch }}</span>
                </div>
                <div v-if="displayedCommit.author" class="commit-detail-row">
                    <span class="commit-detail-key">Author</span>
                    <span class="commit-detail-value">
                        {{ displayedCommit.author.name }}
                        <span v-if="displayedCommit.author.email" class="commit-detail-email">
                            &lt;{{ displayedCommit.author.email }}&gt;
                        </span>
                    </span>
                </div>
                <div class="commit-detail-row">
                    <span class="commit-detail-key">Date</span>
                    <span class="commit-detail-value">{{ displayedCommit.committerDate }}</span>
                </div>
                <div v-if="displayedCommit.parents?.length" class="commit-detail-row">
                    <span class="commit-detail-key">Parents</span>
                    <span class="commit-detail-value">
                        <code v-for="p in displayedCommit.parents" :key="p" class="commit-detail-parent">{{ p }}</code>
                    </span>
                </div>
            </div>
        </div>
    </div>
</template>

<style scoped>
.gitlog-test-view {
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
    background: #ffffff;
    color: #1a1a2e;
    font-family: system-ui, -apple-system, sans-serif;
}

.gitlog-test-view.dark {
    background: #0f0f23;
    color: #e0e0e0;
}

/* ----- Toolbar ----- */

.toolbar {
    flex-shrink: 0;
    padding: 6px 10px;
    border-bottom: 1px solid #d0d0d0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
}

.dark .toolbar {
    border-bottom-color: #333;
}

.toolbar-title {
    font-size: 0.85rem;
    font-weight: 700;
    white-space: nowrap;
}

.control-input,
.control-select {
    padding: 3px 6px;
    border: 1px solid #bbb;
    border-radius: 3px;
    font-size: 0.78rem;
    background: inherit;
    color: inherit;
    height: 26px;
    box-sizing: border-box;
}

.dark .control-input,
.dark .control-select {
    border-color: #555;
    background: #1a1a2e;
}

.control-input {
    width: 140px;
}

.control-input--narrow {
    width: 50px;
}

.control-select--palette {
    width: 155px;
}

.control-select--narrow {
    width: 50px;
}

.control-btn {
    padding: 3px 8px;
    border: 1px solid #bbb;
    border-radius: 3px;
    background: inherit;
    color: inherit;
    cursor: pointer;
    font-size: 0.78rem;
    line-height: 1;
    height: 26px;
    box-sizing: border-box;
}

.control-btn:disabled {
    opacity: 0.3;
    cursor: default;
}

.dark .control-btn {
    border-color: #555;
}

.sizing-group {
    display: flex;
    align-items: center;
    gap: 8px;
}

.page-indicator {
    font-size: 0.78rem;
    font-variant-numeric: tabular-nums;
}

.toggle {
    display: flex;
    align-items: center;
    gap: 3px;
    font-size: 0.78rem;
    cursor: pointer;
    color: inherit;
}

.toggle span {
    color: #1a1a2e;
}

.dark .toggle span {
    color: #e0e0e0;
}

.toggle input {
    cursor: pointer;
}

/* ----- GitLog container ----- */

.gitlog-container {
    flex: 1;
    min-height: 0;
    overflow: auto;
}

/* ----- Commit detail panel ----- */

.commit-detail-panel {
    flex-shrink: 0;
    border-top: 1px solid #d0d0d0;
    padding: 10px 16px;
    font-size: 0.8rem;
    background: #f8f8fc;
    max-height: 180px;
    overflow-y: auto;
    display: none !important;
}

.commit-detail-panel.dark {
    border-top-color: #333;
    background: #161626;
}

.commit-detail-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
}

.commit-detail-badge {
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 2px 6px;
    border-radius: 3px;
    background: #5c6bc0;
    color: #fff;
}

.commit-detail-hash {
    font-family: ui-monospace, monospace;
    font-size: 0.75rem;
    opacity: 0.7;
}

.commit-detail-body {
    display: flex;
    flex-direction: column;
    gap: 3px;
}

.commit-detail-row {
    display: flex;
    gap: 8px;
}

.commit-detail-key {
    font-weight: 600;
    min-width: 60px;
    opacity: 0.6;
}

.commit-detail-value {
    word-break: break-word;
}

.commit-detail-email {
    opacity: 0.5;
}

.commit-detail-parent {
    font-family: ui-monospace, monospace;
    font-size: 0.75rem;
    margin-right: 6px;
    opacity: 0.7;
}
</style>
