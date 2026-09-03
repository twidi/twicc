<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useDataStore } from '../../stores/data.js'
import { useSettingsStore } from '../../stores/settings.js'
import { fetchChangelog, resolveImageLocalUrl, resolveImageGitHubUrl } from '../../utils/changelog.js'
import { renderMarkdown } from '../../utils/markdown.js'
import { openMediaPreview } from '../../composables/useMediaPreview'
import { SPONSOR_URL } from '../../constants'

const emit = defineEmits(['close'])

const store = useDataStore()
const settingsStore = useSettingsStore()

// Sentinel key for the combined "previous → current" entry in the version selector
const COMBINED_VERSION_KEY = '__combined__'

// Category display order for the combined multi-version screen. Each release
// opens with a ### Summary (a single bold-led, one-line recap of the version) —
// a deliberate deviation from Keep a Changelog — followed by the standard
// ### Added / ### Changed / ### Fixed. Entries are grouped by category in this
// fixed order, then by version (oldest first) within each category — so a
// multi-version upgrade reads as "every release's summary first, then all the
// new features, then all the changes, then all the fixes". A fixed list is
// required because a category may appear in only some of the spanned versions,
// leaving document order ambiguous. Any unexpected category is appended
// afterwards in first-appearance order, so no entry is ever dropped.
const COMBINED_CATEGORY_ORDER = ['summary', 'added', 'changed', 'fixed']

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

// The actual sequence of screens the user pages through. Each entry becomes an
// 'entry' slide, interleaved with one or two virtual "support the project"
// 'sponsor' slides:
//   - an intermediate sponsor screen right after the summary block (the leading
//     run of `summary` entries), shown only when at least one non-summary entry
//     follows — so it never duplicates the final one (e.g. a summary-only
//     release like 1.0.0, or a multi-version span with summaries only);
//   - a final sponsor screen, always appended after the last entry.
// With no summary, only the final sponsor screen is shown. The footer counter
// and prev/next navigation are driven by this list's length. An empty changelog
// produces no slides and keeps its dedicated empty state.
const slides = computed(() => {
    const entries = currentVersionEntries.value
    if (!entries.length) return []
    let summaryCount = 0
    while (summaryCount < entries.length && entries[summaryCount].category === 'summary') summaryCount++
    const hasMidSponsor = summaryCount > 0 && summaryCount < entries.length
    const out = []
    entries.forEach((entry, i) => {
        out.push({ type: 'entry', entry })
        if (hasMidSponsor && i === summaryCount - 1) out.push({ type: 'sponsor' })
    })
    out.push({ type: 'sponsor' })
    return out
})

const currentSlide = computed(() => slides.value[currentEntryIdx.value] || null)

const currentEntry = computed(() =>
    currentSlide.value?.type === 'entry' ? currentSlide.value.entry : null
)

const totalScreens = computed(() => slides.value.length)

const isSponsorScreen = computed(() => currentSlide.value?.type === 'sponsor')

const badgeVariant = computed(() => {
    const cat = currentEntry.value?.category
    if (cat === 'summary') return 'brand'
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
    if (isSponsorScreen.value) return 'Enjoying TwiCC?'
    const v = selectedVersion.value
    if (!v) return "What's New"
    if (v === COMBINED_VERSION_KEY) {
        const entry = currentEntry.value
        const prevV = selectedVersionData.value?._previousVersion
        const sourceV = entry?._sourceVersion
        if (prevV && sourceV) {
            return `What's new since v${prevV}: v${sourceV}`
        }
        return "What's New"
    }
    if (/^\d/.test(v)) return `What's New in v${v}`
    return `What's New (${v})`
})

// Find the initial version to display
function findInitialVersion(versionsList) {
    if (!versionsList.length) return ''
    // Prefer combined entry when it exists
    if (versionsList[0]?.version === COMBINED_VERSION_KEY) return COMBINED_VERSION_KEY
    const current = store.currentVersion
    if (current) {
        const match = versionsList.find(v => v.version === current)
        if (match) return match.version
    }
    // Fallback: latest (first in list)
    return versionsList[0].version
}

/**
 * Build a combined version entry spanning all changelogs from after previousVersion
 * up to and including currentVersion. Uses file order (no semver comparison).
 * Returns null if no combined entry should be shown.
 */
function buildCombinedVersion(allVersions, previousVersion, currentVersion) {
    if (!previousVersion || !currentVersion || previousVersion === currentVersion) return null

    const currentIdx = allVersions.findIndex(v => v.version === currentVersion)
    if (currentIdx === -1) return null

    const previousIdx = allVersions.findIndex(v => v.version === previousVersion)
    // If previous not found in changelog, take everything from current to end
    const endIdx = previousIdx === -1 ? allVersions.length : previousIdx

    if (currentIdx >= endIdx) return null

    const versionsInRange = allVersions.slice(currentIdx, endIdx)

    // Reverse to display oldest first (changelog file is newest-first)
    const reversed = [...versionsInRange].reverse()

    // Effective category order: the fixed list above for known categories, then
    // any unexpected ones in first-appearance order so nothing is dropped.
    const presentCategories = []
    for (const v of reversed) {
        for (const entry of v.entries) {
            if (!presentCategories.includes(entry.category)) presentCategories.push(entry.category)
        }
    }
    const orderedCategories = [
        ...COMBINED_CATEGORY_ORDER.filter(c => presentCategories.includes(c)),
        ...presentCategories.filter(c => !COMBINED_CATEGORY_ORDER.includes(c)),
    ]

    // Group by category first, then by version (oldest first) within each
    // category. Entry order inside a same category/version pair is preserved.
    const entries = []
    for (const category of orderedCategories) {
        for (const v of reversed) {
            for (const entry of v.entries) {
                if (entry.category === category) {
                    entries.push({ ...entry, _sourceVersion: v.version })
                }
            }
        }
    }

    if (!entries.length) return null

    return {
        version: COMBINED_VERSION_KEY,
        date: null,
        entries,
        _previousVersion: previousVersion,
        _currentVersion: currentVersion,
    }
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
    if (currentEntryIdx.value < totalScreens.value - 1) currentEntryIdx.value++
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

// Open the clicked image in the shared full-size preview dialog (pan/zoom +
// prev/next navigation across the slide's images). Items are built from the
// rendered <img> siblings so each src reflects any local→GitHub fallback that
// already happened inline (onImageError), matching MarkdownContent's behaviour.
function onImageClick(event) {
    const clicked = event.currentTarget
    const imgs = Array.from(clicked.parentElement.querySelectorAll('img.changelog-image'))
    const items = imgs.map(el => ({
        type: 'image',
        src: el.src,
        name: el.getAttribute('alt') || 'Image',
    }))
    openMediaPreview(items, Math.max(0, imgs.indexOf(clicked)))
}

function versionOptionLabel(v) {
    if (v.version === COMBINED_VERSION_KEY) {
        return `v${v._previousVersion} → v${v._currentVersion}`
    }
    return /^\d/.test(v.version) ? `v${v.version}` : v.version
}

async function open({ skipCombined = false } = {}) {
    loading.value = true
    error.value = null
    versions.value = []
    selectedVersion.value = ''
    currentEntryIdx.value = 0
    renderedHtml.value = ''

    dialogRef.value.open = true

    try {
        const data = await fetchChangelog(settingsStore.isDevMode)
        if (skipCombined) {
            versions.value = data
            // Select the first (latest) version directly
            selectedVersion.value = data.length ? data[0].version : ''
        } else {
            const combined = buildCombinedVersion(data, store.previousChangelogVersion, store.currentVersion)
            versions.value = combined ? [combined, ...data] : data
            selectedVersion.value = findInitialVersion(versions.value)
        }
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

function onDialogHide(event) {
    // Only react to the dialog's own hide event, not bubbled events from
    // child components (e.g. wa-select dropdown closing triggers wa-after-hide too)
    if (event.target !== dialogRef.value) return
    emit('close')
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="dialogLabel"
        class="changelog-dialog"
        @keydown="onKeydown"
        @wa-after-hide="onDialogHide"
    >
        <!-- Loading -->
        <div v-if="loading" class="changelog-loading">
            <wa-spinner style="font-size: 2rem;"></wa-spinner>
            <p>Loading changelog…</p>
        </div>

        <!-- Error -->
        <wa-callout v-else-if="error" variant="danger">{{ error }}</wa-callout>

        <!-- Sponsor screen: always the last screen after the real entries -->
        <div v-else-if="isSponsorScreen" class="changelog-sponsor">
            <wa-icon name="heart" class="changelog-sponsor-heart"></wa-icon>
            <p class="changelog-sponsor-text">
                TwiCC is free and built by a solo developer. If it makes your work
                with Claude Code and Codex smoother, please consider supporting its
                development.
            </p>
            <wa-button
                class="changelog-sponsor-button"
                :href="SPONSOR_URL"
                target="_blank"
                rel="noopener"
            >
                <wa-icon slot="start" name="heart"></wa-icon>
                Sponsor on GitHub
            </wa-button>
            <p class="changelog-sponsor-footnote">Every bit helps — thank you!</p>
        </div>

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
                            @click="onImageClick"
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
                        :label="versionOptionLabel(v)"
                        class="version-option"
                    ><span class="version-option-content"><span class="version-option-name">{{ versionOptionLabel(v) }}</span><span v-if="v.date" class="version-option-date">{{ v.date }}</span></span></wa-option>
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
                    <wa-icon name="chevron-left" slot="start"></wa-icon>
                    Prev
                </wa-button>
                <span class="changelog-counter">
                    {{ totalScreens > 0 ? currentEntryIdx + 1 : 0 }} / {{ totalScreens }}
                </span>
                <wa-button
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    :disabled="currentEntryIdx >= totalScreens - 1"
                    @click="next"
                >
                    Next
                    <wa-icon name="chevron-right" slot="end"></wa-icon>
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

.changelog-sponsor {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 1rem;
    height: 100%;
    text-align: center;
    padding: 0 1rem;
}

.changelog-sponsor-heart {
    font-size: 3rem;
    color: #db61a2; /* GitHub Sponsors pink */
}

.changelog-sponsor-text {
    font-size: var(--wa-font-size-m);
    line-height: 1.7;
    color: var(--wa-color-neutral-700);
    max-width: 32rem;
    margin: 0;
}

.changelog-sponsor-button {
    margin-top: 0.25rem;
}

.changelog-sponsor-button::part(base) {
    background-color: #bf3989; /* GitHub Sponsors pink */
    border-color: #bf3989;
    color: #fff;
}

.changelog-sponsor-button::part(base):hover {
    background-color: #a93578;
    border-color: #a93578;
}

.changelog-sponsor-footnote {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-neutral-500);
    margin: 0;
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
    cursor: pointer;
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

/* The auto margin, not space-between, pushes this group to the right: once the footer
   wraps (narrow screen), space-between would leave this lone second line at the start. */
.changelog-footer-right {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-inline-start: auto;
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
