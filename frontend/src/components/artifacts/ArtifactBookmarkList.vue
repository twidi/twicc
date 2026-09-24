<script setup>
// ArtifactBookmarkList.vue — the sidebar list in Artifacts mode. The
// artifacts-mode counterpart of SessionList: a flat, recency-sorted list of
// bookmarked artifacts visible in the current sidebar scope (project /
// workspace / all), resolved by computeArtifactBookmarkList (worktree-aware,
// mirroring session pins) and
// filtered by the shared matchQuery util on name + relative_path.
//
// Rows reuse the exact session-list styling and link behaviour: each row is a
// wa-button rendered as an anchor, `plain`/`neutral` by default and
// `outlined`/`brand` when it is the artifact currently open in the main pane
// (--active), with a focus-ring --highlighted state for keyboard navigation.
// Bookmark lists are small, so there is no virtual scroller — but the
// keyboard-navigation contract mirrors SessionList (handleKeyNavigation
// exposed, focus-search emitted on ArrowUp from the top).
import { computed, ref, watch, nextTick, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { useSettingsStore } from '../../stores/settings'
import { computeArtifactBookmarkList } from '../../utils/sidebarArtifactBookmarks'
import { matchQuery } from '../../utils/textFilter'
import { artifactBookmarkRouteLocation, artifactTypeIcon } from '../../utils/artifactBookmark'
import { buildFilesRouteParams } from '../../utils/granularRoutes'
import { formatDate } from '../../utils/date'
import { dateBucketSeparator } from '../../utils/datePresets'
import { SESSION_TIME_FORMAT } from '../../constants'
import ProjectBadge from '../project/ProjectBadge.vue'
import ProjectMark from '../project/ProjectMark.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import ArtifactBookmarkDialog from './ArtifactBookmarkDialog.vue'
import ArtifactsHelpButton from './ArtifactsHelpButton.vue'
import SidebarListSeparator from '../sidebar/SidebarListSeparator.vue'

const props = defineProps({
    effectiveProjectId: { type: String, default: null },
    activeWorkspaceId: { type: String, default: null },
    searchQuery: { type: String, default: '' },
    compactView: { type: Boolean, default: false },
    // When true, ignore the current scope and list every bookmark (still sorted
    // by recency). Driven by the "Show all (ignore scope)" options toggle.
    showAllArtifacts: { type: Boolean, default: false },
    // The bookmark currently open in the main pane (route param). Marks the
    // matching row as --active (outlined/brand), like the selected session.
    activeBookmarkId: { type: [String, Number], default: null },
})

const emit = defineEmits(['select', 'focus-search'])

const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()
const settingsStore = useSettingsStore()
const route = useRoute()
const router = useRouter()

const list = computed(() => {
    let rows = computeArtifactBookmarkList({
        bookmarks: dataStore.artifactBookmarks,
        workspaces: workspacesStore,
        effectiveProjectId: props.effectiveProjectId,
        activeWorkspaceId: props.activeWorkspaceId,
        mainProjectId: dataStore.getMainRepoProjectId(props.effectiveProjectId),
        showAll: props.showAllArtifacts,
    })
    const q = props.searchQuery.trim()
    if (q) rows = rows.filter(b => matchQuery(q, b.name) || matchQuery(q, b.relative_path))
    return rows
})

// Publish the rendered count (search filter included) so ProjectView's
// `data-has-items` presence flag tracks the artifacts mode exactly as
// `displayedSessionIds` does for sessions. Cleared on unmount so a stale count
// never lingers when the sidebar is gone.
watch(() => list.value.length, (count) => {
    dataStore.setDisplayedArtifactBookmarkCount(count)
}, { immediate: true })
onBeforeUnmount(() => dataStore.setDisplayedArtifactBookmarkCount(0))

/** Whether a bookmark is the one currently open in the main pane. */
function isActive(b) {
    return props.activeBookmarkId != null && String(b.id) === String(props.activeBookmarkId)
}

/**
 * Color for the compact-mode project dot. A worktree with no color of its own
 * inherits its main repository's color (matching SessionListItem).
 */
function dotColor(b) {
    const p = dataStore.getProject(b.project_id)
    if (!p) return null
    if (p.worktree_of) return p.color || dataStore.getProject(p.worktree_of)?.color || null
    return p.color || null
}

/** Icon URL for the bookmark's project (server-resolved), else null → color dot. */
function iconUrl(b) {
    return dataStore.resolvedProjectIcons[b.project_id] || null
}

// Last-update time — rendered exactly like the session list (clock icon +
// relative or smart-absolute time, per the same user setting).
const sessionTimeFormat = computed(() => settingsStore.getSessionTimeFormat)
const useRelativeTime = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ||
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_NARROW,
)
const relativeTimeFormat = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow',
)
function updatedDate(b) {
    return b.updated_at ? new Date(b.updated_at) : new Date()
}
function updatedTs(b) {
    return b.updated_at ? Math.floor(new Date(b.updated_at).getTime() / 1000) : 0
}

// Date separators: walk the recency-sorted list and mark the first bookmark of
// each date bucket (Last 24 hours, then the "older than <X>" thresholds — the
// same system as the session list, dates only). Keyed off `updated_at` (the sort
// field) so the separators follow the list order. A Map of bookmark id ->
// SidebarListSeparator props, consumed in the v-for to render one before the
// first item of each bucket.
const separatorBeforeIds = computed(() => {
    const map = new Map()
    const nowMs = Date.now()
    let prevKey = null
    for (const b of list.value) {
        const { key, entry } = dateBucketSeparator(updatedTs(b), nowMs)
        if (key !== prevKey) {
            prevKey = key
            map.set(b.id, entry)
        }
    }
    return map
})

function onSelect(b) {
    emit('select', b)
}

// Real hrefs give every row native browser link behaviour (context menu,
// middle-click, Ctrl/Cmd-click, Shift-click). Plain left-click remains an SPA
// navigation through ProjectView's existing selection handler.
function bookmarkHref(b) {
    return router.resolve(artifactBookmarkRouteLocation(b, route)).href
}

function handleClick(event, b) {
    if (event.button !== 0) return
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    onSelect(b)
}

// ═══════════════════════════════════════════════════════════════════════════
// Row actions — the same four the ArtifactsBrowserView header offers, so a
// bookmark can be managed without opening it first.
// ═══════════════════════════════════════════════════════════════════════════

// Share is gated on a configured share host, and routes through the
// globally-mounted artifact ShareDialog (ProjectView) via the shared window
// event, so the list never mounts a share dialog per row.
const sharingEnabled = computed(() => !!settingsStore.getUsableShareBaseUrl)
function openShare(b) {
    window.dispatchEvent(new CustomEvent('twicc:open-share-dialog', {
        detail: { bookmarkId: b.id, allowedHosts: b.allowed_hosts || {}, title: b.name || '' },
    }))
}

// Edit reuses the create/edit dialog, mounted ONCE for the whole list and
// retargeted at the row being edited (its props are per-bookmark), rather than
// one instance per row.
const editDialogRef = ref(null)
const editBookmark = ref(null)
async function openEdit(b) {
    editBookmark.value = b
    await nextTick()
    editDialogRef.value?.open(b)
}

// Mirrors ArtifactsBrowserView: derive the route family from the route name so
// navigation stays within the current (project / all-projects) mode.
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))
const queryWithWorkspace = () => (route.query.workspace ? { workspace: route.query.workspace } : {})

/** Leave the artifact pane empty — only when the removed row was the open one. */
function backToList() {
    if (isAllProjectsMode.value) {
        router.push({ name: 'projects-artifacts', query: queryWithWorkspace() })
    } else {
        router.push({ name: 'project-artifacts', params: { projectId: route.params.projectId } })
    }
}

// Open the source session's Artifacts tab with this file revealed.
// buildFilesRouteParams applies encodePath internally, so the RAW relative_path
// is passed.
function openInSession(b) {
    const params = buildFilesRouteParams({ rootKey: 'artifacts', filePath: b.relative_path })
    const name = isAllProjectsMode.value ? 'projects-session-artifacts' : 'session-artifacts'
    router.push({
        name,
        params: { projectId: b.project_id, sessionId: b.session_id, ...params },
        query: queryWithWorkspace(),
    })
}

async function removeBookmark(b) {
    const wasActive = isActive(b)
    try {
        await dataStore.deleteArtifactBookmark(b.id)
    } catch {
        // Even on failure, the bookmark is gone server-side or the store will
        // resync via WS — the row leaves the list either way.
    }
    if (wasActive) backToList()
}

/** The dialog removed the bookmark it was opened on. */
function onRemovedFromDialog() {
    if (editBookmark.value && isActive(editBookmark.value)) backToList()
}

function handleMenuSelect(event, b) {
    const action = event.detail.item.value
    if (action === 'edit') {
        openEdit(b)
    } else if (action === 'share') {
        openShare(b)
    } else if (action === 'open-session') {
        openInSession(b)
    } else if (action === 'remove') {
        removeBookmark(b)
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Keyboard navigation (mirrors SessionList, without the virtual scroller)
// ═══════════════════════════════════════════════════════════════════════════

const listRef = ref(null)
const highlightedIndex = ref(-1)
const PAGE_SIZE = 10

/** Scroll the row at `index` into view (no virtual scroller — plain DOM). */
function scrollRowIntoView(index) {
    if (index < 0) return
    nextTick(() => {
        const rows = listRef.value?.querySelectorAll(':scope > .bookmark-item-wrapper')
        rows?.[index]?.scrollIntoView({ block: 'nearest' })
    })
}

/**
 * Starting index for keyboard navigation: the current highlight if any,
 * otherwise the active (open) bookmark, otherwise none.
 */
function getNavigationStartIndex() {
    if (highlightedIndex.value >= 0) return highlightedIndex.value
    if (props.activeBookmarkId != null) {
        const i = list.value.findIndex(b => isActive(b))
        if (i >= 0) return i
    }
    return -1
}

/**
 * Handle keyboard navigation from the filter input or the list itself.
 *
 * @param {KeyboardEvent} event
 * @param {Object} [options]
 * @param {boolean} [options.fromSearch=false] - True when called from the
 *   filter input: navigation always starts from scratch (ArrowDown → first row)
 *   rather than relative to the active bookmark.
 * @returns {boolean} True if the event was handled (caller should preventDefault).
 */
function handleKeyNavigation(event, { fromSearch = false } = {}) {
    const count = list.value.length
    if (count === 0) return false

    const key = event.key
    const startIndex = (fromSearch && highlightedIndex.value < 0) ? -1 : getNavigationStartIndex()
    let newIndex = highlightedIndex.value

    switch (key) {
        case 'ArrowDown':
            newIndex = startIndex < 0 ? 0 : Math.min(startIndex + 1, count - 1)
            break

        case 'ArrowUp':
            // At the first row, ArrowUp returns focus to the filter input.
            if (startIndex === 0) {
                highlightedIndex.value = -1
                emit('focus-search')
                return true
            }
            newIndex = startIndex < 0 ? count - 1 : Math.max(startIndex - 1, 0)
            break

        case 'Home':
            newIndex = 0
            break

        case 'End':
            newIndex = count - 1
            break

        case 'PageDown':
            newIndex = startIndex < 0 ? PAGE_SIZE - 1 : Math.min(startIndex + PAGE_SIZE, count - 1)
            break

        case 'PageUp':
            if (startIndex === 0) {
                highlightedIndex.value = -1
                emit('focus-search')
                return true
            }
            newIndex = startIndex < 0 ? 0 : Math.max(startIndex - PAGE_SIZE, 0)
            break

        case 'Enter':
            if (highlightedIndex.value >= 0 && highlightedIndex.value < count) {
                onSelect(list.value[highlightedIndex.value])
                return true
            }
            return false

        case 'Escape':
            if (highlightedIndex.value >= 0) {
                highlightedIndex.value = -1
                return true
            }
            return false

        default:
            return false
    }

    if (newIndex !== highlightedIndex.value) {
        highlightedIndex.value = newIndex
        // Keep focus on the list so subsequent keys keep navigating, and bring
        // the highlighted row into view.
        nextTick(() => {
            scrollRowIntoView(newIndex)
            listRef.value?.focus()
        })
    }
    return true
}

/** Keydown handler bound to the list container (focus is in the list). */
function handleListKeydown(event) {
    const navigationKeys = ['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageUp', 'PageDown', 'Enter', 'Escape']
    if (!navigationKeys.includes(event.key)) return
    if (handleKeyNavigation(event)) event.preventDefault()
}

// Reset the highlight whenever the list contents change out from under it.
watch(() => props.searchQuery, () => { highlightedIndex.value = -1 })
watch(() => props.effectiveProjectId, () => { highlightedIndex.value = -1 })
watch(() => props.activeWorkspaceId, () => { highlightedIndex.value = -1 })

// When the open bookmark changes, drop any keyboard highlight and reveal the
// newly-active row (mirrors SessionList scrolling to the selected session).
watch(() => props.activeBookmarkId, (id) => {
    highlightedIndex.value = -1
    if (id == null) return
    const i = list.value.findIndex(b => isActive(b))
    if (i >= 0) scrollRowIntoView(i)
}, { immediate: true })

defineExpose({ handleKeyNavigation })
</script>

<template>
    <div class="bookmark-list-container">
        <div v-if="list.length === 0" class="empty-state">
            <template v-if="searchQuery.trim()">No artifacts in this scope match your filter.</template>
            <template v-else>
                <div>No bookmarked artifacts in this scope yet — bookmark an artifact from a session's Artifacts tab.</div>
                <ArtifactsHelpButton />
            </template>
        </div>
        <div
            v-else
            ref="listRef"
            class="bookmark-list"
            tabindex="0"
            @keydown="handleListKeydown"
        >
            <template v-for="(b, index) in list" :key="b.id">
            <SidebarListSeparator
                v-if="separatorBeforeIds.has(b.id)"
                v-bind="separatorBeforeIds.get(b.id)"
            />
            <div
                class="bookmark-item-wrapper"
                :class="{ 'bookmark-item-wrapper--compact': compactView }"
            >
            <wa-button
                :href="bookmarkHref(b)"
                :appearance="isActive(b) ? 'outlined' : 'plain'"
                :variant="isActive(b) ? 'brand' : 'neutral'"
                class="bookmark-item"
                :class="{
                    'bookmark-item--active': isActive(b),
                    'bookmark-item--highlighted': index === highlightedIndex,
                    'bookmark-item--compact': compactView,
                }"
                @click="(event) => handleClick(event, b)"
            >
                <div class="bookmark-name-row">
                    <!-- Compact: inline project color dot (worktree inherits its parent's color) -->
                    <ProjectMark
                        v-if="compactView"
                        :id="`bookmark-dot-${b.id}`"
                        :icon-url="iconUrl(b)"
                        :color="dotColor(b)"
                    />
                    <AppTooltip v-if="compactView" :for="`bookmark-dot-${b.id}`">
                        <ProjectBadge :project-id="b.project_id" :dot="false" />
                    </AppTooltip>
                    <!-- File-type icon, before the name (both modes) -->
                    <wa-icon class="bookmark-type" :name="artifactTypeIcon(b.file_ext)"></wa-icon>
                    <span class="bookmark-name">{{ b.name }}</span>
                </div>
                <!-- Non-compact: project badge (left) + last-update time (right) -->
                <div v-if="!compactView" class="bookmark-meta">
                    <ProjectBadge :project-id="b.project_id" class="bookmark-project" />
                    <span class="bookmark-time">
                        <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                        <wa-relative-time
                            v-if="useRelativeTime"
                            :date.prop="updatedDate(b)"
                            :format="relativeTimeFormat"
                            numeric="always"
                            sync
                        ></wa-relative-time>
                        <template v-else>{{ formatDate(updatedTs(b), { smart: true }) }}</template>
                    </span>
                </div>
            </wa-button>
            <!-- Row actions menu (outside the button to avoid nesting), mirroring
                 the ArtifactsBrowserView header. Share is gated on a configured
                 share host. -->
            <wa-dropdown
                class="bookmark-menu"
                placement="bottom-end"
                @wa-select="(e) => handleMenuSelect(e, b)"
            >
                <wa-button
                    :id="`bookmark-menu-trigger-${b.id}`"
                    slot="trigger"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="bookmark-menu-trigger"
                >
                    <wa-icon name="ellipsis-v" label="Artifact actions"></wa-icon>
                </wa-button>
                <wa-dropdown-item value="edit">
                    <wa-icon slot="icon" name="pencil"></wa-icon>
                    Edit…
                </wa-dropdown-item>
                <wa-dropdown-item v-if="sharingEnabled" value="share">
                    <wa-icon slot="icon" name="share-nodes"></wa-icon>
                    Share…
                </wa-dropdown-item>
                <wa-dropdown-item value="open-session">
                    <wa-icon slot="icon" name="house"></wa-icon>
                    Open in session
                </wa-dropdown-item>
                <wa-divider></wa-divider>
                <wa-dropdown-item value="remove" variant="danger">
                    <wa-icon slot="icon" name="trash"></wa-icon>
                    Remove bookmark
                </wa-dropdown-item>
            </wa-dropdown>
            <AppTooltip :for="`bookmark-menu-trigger-${b.id}`">Artifact actions</AppTooltip>
            </div>
            </template>
        </div>

        <!-- Edit dialog, retargeted at the row being edited (one instance for the
             whole list). -->
        <ArtifactBookmarkDialog
            v-if="editBookmark"
            ref="editDialogRef"
            :session-id="editBookmark.session_id"
            :relative-path="editBookmark.relative_path"
            @removed="onRemovedFromDialog"
        />
    </div>
</template>

<style scoped>
.bookmark-list-container {
    flex: 1;
    min-height: 0;
    width: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    container-type: inline-size;
    container-name: bookmark-list;
}

.bookmark-list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: var(--wa-space-2xs);
    display: flex;
    flex-direction: column;
}

/* Highlight is shown on the items, not the list itself. */
.bookmark-list:focus {
    outline: none;
}

/* Row wrapper: positioning context for the absolutely-placed actions menu, and
   the flex child that now carries the inter-row gap (moved off the button so the
   menu overlays the row cleanly, mirroring SessionListItem). */
.bookmark-item-wrapper {
    position: relative;
    width: 100%;
}
.bookmark-item-wrapper:not(.bookmark-item-wrapper--compact) {
    margin-block: var(--wa-space-3xs);
}

.bookmark-item {
    width: 100%;
}

/* Actions menu, tucked into the row's top-right corner and revealed on hover
   (or while open), exactly like the session list's row menu. */
.bookmark-menu {
    display: block;
    position: absolute;
    top: var(--wa-space-2xs);
    right: var(--wa-space-xs);
    z-index: 1;
}
.bookmark-item-wrapper--compact .bookmark-menu {
    top: 0;
}
.bookmark-menu-trigger {
    opacity: 0.4;
    transition: opacity 0.15s;
    font-size: var(--wa-font-size-2xs);
}
.bookmark-item-wrapper:hover .bookmark-menu-trigger,
.bookmark-menu[open] .bookmark-menu-trigger {
    opacity: 0.6;
}
.bookmark-menu-trigger:hover {
    opacity: 1 !important;
}

.bookmark-item::part(base) {
    padding: var(--wa-space-xs);
    height: auto;
    /* Reserve the outlined-appearance shadow offset so the active row doesn't
       shift the others (matches SessionListItem). */
    margin-bottom: var(--wa-shadow-offset-y-s);
}

.bookmark-item--compact::part(base) {
    padding-block: var(--wa-space-2xs);
}

.bookmark-item::part(label) {
    width: 100%;
    text-align: left;
}

/* Keyboard navigation highlight */
.bookmark-item--highlighted::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

.bookmark-name-row {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: var(--wa-space-xs);
    width: 100%;
    min-width: 0;
}

/* Compact-mode project color dot (mirrors SessionListItem's compact dot). */

.bookmark-type {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.bookmark-name {
    font-size: var(--wa-font-size-s);
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    /* Visual space for the actions menu overlaying the row's top-right corner,
       so a long name ellipsizes before it rather than running underneath
       (matches SessionListItem). */
    margin-right: 1.5rem;
}

.bookmark-meta {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: var(--wa-space-xs);
    width: 100%;
    min-width: 0;
    margin-top: var(--wa-space-3xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-body);
}

.bookmark-project {
    min-width: 0;
    overflow: hidden;
}

.bookmark-time {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    flex-shrink: 0;
    margin-left: auto;
}

.empty-state {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-m);
    text-align: center;
    padding: var(--wa-space-l);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-m);
}
</style>
