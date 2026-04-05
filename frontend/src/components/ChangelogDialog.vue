<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useDataStore } from '../stores/data.js'
import { fetchChangelog, resolveImageLocalUrl, resolveImageGitHubUrl } from '../utils/changelog.js'
import { renderMarkdown } from '../utils/markdown.js'

const store = useDataStore()

const dialogRef = ref(null)
const loading = ref(false)
const error = ref(null)
const versions = ref([])
const selectedVersion = ref('')
const currentEntryIdx = ref(0)
const renderedHtml = ref('')

const selectedVersionData = computed(() =>
    versions.value.find(v => v.version === selectedVersion.value) || null
)

const currentVersionEntries = computed(() =>
    selectedVersionData.value?.entries || []
)

const currentEntry = computed(() => {
    const entries = currentVersionEntries.value
    if (!entries.length) return null
    return entries[currentEntryIdx.value] || null
})

const badgeVariant = computed(() => {
    const cat = currentEntry.value?.category
    if (cat === 'added') return 'success'
    if (cat === 'changed') return 'warning'
    if (cat === 'fixed') return 'danger'
    return 'neutral'
})

const badgeLabel = computed(() => {
    const cat = currentEntry.value?.category
    if (!cat) return ''
    return cat.charAt(0).toUpperCase() + cat.slice(1)
})

const dialogLabel = computed(() => {
    const v = selectedVersion.value
    if (!v) return "What's New"
    if (/^\d/.test(v)) return `What's New in v${v}`
    return `What's New (${v})`
})

// Find the initial version to display
function findInitialVersion(versionsList) {
    if (!versionsList.length) return ''
    const current = store.currentVersion
    if (current) {
        const match = versionsList.find(v => v.version === current)
        if (match) return match.version
    }
    // Fallback: latest (first in list)
    return versionsList[0].version
}

async function renderCurrentEntry() {
    const entry = currentEntry.value
    if (!entry) {
        renderedHtml.value = ''
        return
    }
    renderedHtml.value = await renderMarkdown(entry.text)
}

watch(currentEntry, renderCurrentEntry)

// When version changes, reset to first entry
watch(selectedVersion, () => {
    currentEntryIdx.value = 0
})

function prev() {
    if (currentEntryIdx.value > 0) currentEntryIdx.value--
}

function next() {
    if (currentEntryIdx.value < currentVersionEntries.value.length - 1) currentEntryIdx.value++
}

function onVersionChange(event) {
    selectedVersion.value = event.target.value
}

// Image fallback: try local, on error switch to GitHub
function onImageError(event, img) {
    const githubUrl = resolveImageGitHubUrl(img.path)
    // Only fallback once to avoid infinite loop
    if (event.target.src !== githubUrl) {
        event.target.src = githubUrl
    }
}

async function open() {
    loading.value = true
    error.value = null
    versions.value = []
    selectedVersion.value = ''
    currentEntryIdx.value = 0
    renderedHtml.value = ''

    dialogRef.value.open = true

    try {
        const data = await fetchChangelog()
        versions.value = data
        selectedVersion.value = findInitialVersion(data)
        await nextTick()
        await renderCurrentEntry()
    } catch (e) {
        error.value = `Could not load changelog: ${e.message}`
    } finally {
        loading.value = false
    }
}

function close() {
    dialogRef.value.open = false
}

// Keyboard navigation
function onKeydown(event) {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
        event.preventDefault()
        prev()
    } else if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
        event.preventDefault()
        next()
    }
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="dialogLabel"
        class="changelog-dialog"
        @keydown="onKeydown"
    >
        <!-- Loading -->
        <div v-if="loading" class="changelog-loading">
            <wa-spinner style="font-size: 2rem;"></wa-spinner>
            <p>Loading changelog…</p>
        </div>

        <!-- Error -->
        <wa-callout v-else-if="error" variant="danger">{{ error }}</wa-callout>

        <!-- Empty -->
        <div v-else-if="!currentEntry" class="changelog-empty">
            <p>No changelog entries found.</p>
        </div>

        <!-- Entry content -->
        <div v-else class="changelog-entry">
            <wa-badge :variant="badgeVariant" class="changelog-badge">{{ badgeLabel }}</wa-badge>
            <div class="changelog-entry-content-scroll">
                <div class="changelog-entry-content">
                    <div class="changelog-text markdown-body" v-html="renderedHtml"></div>
                    <div v-if="currentEntry.images.length" class="changelog-images">
                        <img
                            v-for="(img, idx) in currentEntry.images"
                            :key="currentEntryIdx + '-' + idx"
                            :src="resolveImageLocalUrl(img.path)"
                            :alt="img.alt"
                            loading="lazy"
                            class="changelog-image"
                            @error="onImageError($event, img)"
                        />
                    </div>
                </div>
            </div>
        </div>

        <!-- Footer -->
        <div slot="footer" class="changelog-footer" v-show="versions.length">
            <div class="changelog-footer-left">
                <wa-select
                    v-if="versions.length"
                    size="small"
                    :value.prop="selectedVersion"
                    @change="onVersionChange"
                    class="changelog-version-select"
                >
                    <wa-option
                        v-for="v in versions"
                        :key="v.version"
                        :value="v.version"
                        :label="/^\d/.test(v.version) ? `v${v.version}` : v.version"
                        class="version-option"
                    ><span class="version-option-content"><span class="version-option-name">{{ /^\d/.test(v.version) ? `v${v.version}` : v.version }}</span><span v-if="v.date" class="version-option-date">{{ v.date }}</span></span></wa-option>
                </wa-select>
            </div>
            <div class="changelog-footer-right">
                <wa-button
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    :disabled="currentEntryIdx <= 0"
                    @click="prev"
                >
                    <wa-icon name="chevron-left" slot="prefix"></wa-icon>
                    Prev
                </wa-button>
                <span class="changelog-counter">
                    {{ currentVersionEntries.length > 0 ? currentEntryIdx + 1 : 0 }} / {{ currentVersionEntries.length }}
                </span>
                <wa-button
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    :disabled="currentEntryIdx >= currentVersionEntries.length - 1"
                    @click="next"
                >
                    Next
                    <wa-icon name="chevron-right" slot="suffix"></wa-icon>
                </wa-button>
            </div>
        </div>
    </wa-dialog>
</template>

<style scoped>
.changelog-dialog {
    --width: min(40rem, calc(100vw - 2rem));
}

.changelog-dialog::part(body) {
    height: min(24rem, calc(100dvh - 10rem));
    overflow: hidden;
    padding-top: 0;
}

.changelog-loading {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 1rem;
    height: 100%;
    color: var(--wa-color-neutral-500);
}

.changelog-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--wa-color-neutral-500);
}

.changelog-entry {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    height: 100%;
    min-height: 0;
}
.changelog-entry-content-scroll {
    overflow-y: auto;
    flex: 1;
    height: 100%;
}
.changelog-entry-content {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    flex: 1;
    min-height: 100%;
}

.changelog-badge {
    align-self: flex-start;
}

.changelog-text {
    font-size: var(--wa-font-size-m);
    line-height: 1.8;
    margin-top: auto;
    &:last-child {
        margin-bottom: auto;
    }
}

/* Override markdown-body defaults for this context */
.changelog-text.markdown-body {
    font-size: var(--wa-font-size-m);
}

.changelog-images {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    margin-top: 1rem;
    margin-bottom: auto;
    align-items: center;
}

.changelog-image {
    max-width: 100%;
    max-height: 100%;
    width: auto;
    height: auto;
    border-radius: 8px;
    border: 1px solid var(--wa-color-neutral-200);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

html.wa-dark .changelog-image {
    border-color: var(--wa-color-neutral-700);
}

.changelog-footer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    width: 100%;
}

.changelog-footer-left {
    flex-shrink: 0;
}

.changelog-version-select {
    min-width: 200px;
}

.changelog-footer-right {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-shrink: 0;
}

.changelog-counter {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-neutral-500);
    min-width: 3.5em;
    text-align: center;
    font-variant-numeric: tabular-nums;
}
</style>

<style>
.changelog-footer {
    /* Version options — unscoped to penetrate wa-option shadow DOM */

    .version-option-content {
        display: flex;
        justify-content: space-between;
        align-items: center;
        width: 100%;
        gap: 1rem;
    }

    .version-option-name {
        font-family: var(--wa-font-family-code);
    }

    .version-option-date {
        color: var(--wa-color-text-quiet);
        font-size: var(--wa-font-size-s);
        flex-shrink: 0;
    }
}
</style>
