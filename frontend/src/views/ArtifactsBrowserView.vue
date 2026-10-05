<script setup>
// ArtifactsBrowserView.vue — the main pane in Artifacts mode. Renders a
// selected bookmark's artifact in a render-only FilePane, with a header to
// edit / remove the bookmark and jump to its source session. Orphan detection
// is lazy on open: fetchArtifactBookmarkDetail GETs a fresh server-side `available`
// flag and the missing-file callout replaces the render when it comes back
// false.
//
// Rendered directly by ProjectView (not through the session <router-view>,
// which is keyed to sessionId) — the artifacts route component is a
// { render: () => null } stub. See the design doc §7/§8.
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { useSharesStore } from '../stores/shares'
import { isShareOutdated } from '../utils/shareStatus'
import { useNetworkDenials } from '../composables/useNetworkDenials'
import { buildFilesRouteParams } from '../utils/granularRoutes'
import FilePane from '../components/files/FilePane.vue'
import ArtifactBookmarkDialog from '../components/artifacts/ArtifactBookmarkDialog.vue'
import ArtifactsHelpButton from '../components/artifacts/ArtifactsHelpButton.vue'
import AppTooltip from '../components/ui/AppTooltip.vue'
import { ARTIFACT_ICON, artifactTypeIcon } from '../utils/artifactBookmark'

const props = defineProps({
    bookmarkId: { type: [String, Number], default: null },
    effectiveProjectId: { type: String, default: null },
})

const route = useRoute()
const router = useRouter()
const dataStore = useDataStore()
const settingsStore = useSettingsStore()
const sharesStore = useSharesStore()

const editDialogRef = ref(null)

// Lifecycle flags for the current bookmarkId. `available` is null until the
// detail GET resolves; false → missing-file callout; true → render.
const loading = ref(false)
const notFound = ref(false)
const available = ref(null)

const bookmark = computed(() =>
    props.bookmarkId != null ? dataStore.artifactBookmarks[props.bookmarkId] || null : null
)

// Share entry point — routes through the globally-mounted artifact ShareDialog
// (ProjectView) so there's a single dialog instance. Disabled until a share host
// is configured; badge mirrors the session header's active-link count.
const sharingEnabled = computed(() => !!settingsStore.getUsableShareBaseUrl)
const activeShareCount = computed(() =>
    bookmark.value ? sharesStore.activeCountForBookmark(bookmark.value.id) : 0
)
// Links serving a stale copy → the share button warns so the owner pushes the update.
const outdatedShareCount = computed(() =>
    bookmark.value ? sharesStore.forBookmark(bookmark.value.id).filter(isShareOutdated).length : 0
)
const shareVariant = computed(() =>
    outdatedShareCount.value > 0 ? 'warning' : (activeShareCount.value > 0 ? 'brand' : 'neutral')
)
// Broker hosts awaiting an allow/deny decision → the edit button (which opens the
// Network access section) warns + pulses.
const { pendingCount } = useNetworkDenials(bookmark)
function openShare() {
    if (!bookmark.value || !sharingEnabled.value) return
    window.dispatchEvent(new CustomEvent('twicc:open-share-dialog', {
        detail: {
            bookmarkId: bookmark.value.id,
            allowedHosts: bookmark.value.allowed_hosts || {},
            title: bookmark.value.name || '',
        },
    }))
}

// Derive mode from the route name (matches ProjectView's isAllProjectsMode),
// so navigation targets the right route family regardless of props timing.
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))
const queryWithWorkspace = () => (route.query.workspace ? { workspace: route.query.workspace } : {})

const filePath = computed(() =>
    bookmark.value ? `${bookmark.value.root}/${bookmark.value.relative_path}` : ''
)

const filePaneRef = ref(null)
// Cache up to this many rendered artifacts, each in its OWN FilePane instance
// (keyed by bookmark id) — so its persistent-frame iframe survives a switch to
// another artifact and back with no reload. Capped to bound the live iframes.
const MAX_CACHED_ARTIFACTS = 5
// HTML artifacts get a reload control in the header (reloads the rendered iframe).
const isHtml = computed(() => /\.html?$/i.test(bookmark.value?.relative_path || ''))
function reloadPreview() {
    filePaneRef.value?.reloadHtmlPreview?.()
}

async function loadDetail(id) {
    notFound.value = false
    // Optional polish: if metadata is already cached (boot/list load), render it
    // immediately and verify availability in the background — no loading flash
    // when navigating from the list. Otherwise show a loading state for the
    // deep-link case (id not yet in the store).
    const cached = dataStore.artifactBookmarks[id]
    if (cached) {
        available.value = true
        loading.value = false
    } else {
        available.value = null
        loading.value = true
    }
    const detail = await dataStore.fetchArtifactBookmarkDetail(id)
    // Ignore a stale resolution if the user navigated to another bookmark mid-flight.
    if (String(props.bookmarkId) !== String(id)) return
    loading.value = false
    if (detail === null) {
        // fetchArtifactBookmarkDetail returns null both on a real 404 (it then DROPS the
        // bookmark from the store) and on a transient error (network/500 — the
        // store entry is kept). Distinguish via the store: if the bookmark is
        // still cached, it was a transient error — keep showing the cached
        // version rather than a false permanent-deletion message. Only a
        // genuinely-gone bookmark (no store entry) is "not found".
        if (dataStore.artifactBookmarks[id]) {
            available.value = true
        } else {
            notFound.value = true
            available.value = null
        }
        return
    }
    available.value = detail.available !== false
}

watch(
    () => props.bookmarkId,
    (id) => {
        if (id == null) {
            loading.value = false
            notFound.value = false
            available.value = null
            return
        }
        loadDetail(id)
    },
    { immediate: true }
)

function openEdit() {
    if (bookmark.value) editDialogRef.value?.open(bookmark.value)
}

function backToList() {
    if (isAllProjectsMode.value) {
        router.push({ name: 'projects-artifacts', query: queryWithWorkspace() })
    } else {
        router.push({ name: 'project-artifacts', params: { projectId: route.params.projectId } })
    }
}

async function removeArtifactBookmark() {
    if (!bookmark.value) return
    try {
        await dataStore.deleteArtifactBookmark(bookmark.value.id)
    } catch {
        // Even on failure, the bookmark is gone server-side or the store will
        // resync via WS; navigating back to the list is the correct outcome.
    }
    backToList()
}

function onRemovedFromDialog() {
    backToList()
}

// Open the source session's Artifacts tab with this file revealed, mirroring
// SessionView.onArtifactsNavigate. buildFilesRouteParams applies encodePath
// internally, so the RAW relative_path is passed.
function openInSession() {
    if (!bookmark.value) return
    const params = buildFilesRouteParams({ rootKey: 'artifacts', filePath: bookmark.value.relative_path })
    const name = isAllProjectsMode.value ? 'projects-session-artifacts' : 'session-artifacts'
    router.push({
        name,
        params: {
            projectId: bookmark.value.project_id,
            sessionId: bookmark.value.session_id,
            ...params,
        },
        query: queryWithWorkspace(),
    })
}
</script>

<template>
    <div class="artifacts-browser">
        <!-- No bookmark selected -->
        <div v-if="bookmarkId == null" class="abv-empty panel-card">
            <wa-icon :name="ARTIFACT_ICON" class="abv-empty__icon"></wa-icon>
            <div>Select an artifact</div>
            <ArtifactsHelpButton />
        </div>

        <!-- Loading (deep-link, not yet in the store) -->
        <div v-else-if="loading && !bookmark" class="abv-empty panel-card">
            <wa-spinner></wa-spinner>
            <span>Loading…</span>
        </div>

        <!-- Unknown bookmark id -->
        <div v-else-if="notFound" class="abv-empty panel-card">
            <wa-callout variant="warning" size="small">
                <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                This bookmark no longer exists.
                <div class="abv-callout-actions">
                    <wa-button size="small" variant="neutral" appearance="outlined" @click="backToList">
                        Back to bookmarks
                    </wa-button>
                </div>
            </wa-callout>
        </div>

        <!-- Resolved bookmark -->
        <template v-else-if="bookmark">
            <header class="abv-header">
                <div class="abv-header__title">
                    <wa-icon :name="artifactTypeIcon(bookmark.file_ext)"></wa-icon>
                    <span class="abv-header__name" :title="bookmark.relative_path">{{ bookmark.name }}</span>
                    <wa-button
                        id="abv-edit-button"
                        size="small"
                        :variant="pendingCount > 0 ? 'warning' : 'neutral'"
                        appearance="plain"
                        :class="['abv-title-btn', 'reduced-height', { 'abv-title-btn--attention': pendingCount > 0 }]"
                        @click="openEdit"
                    >
                        <wa-icon name="pencil" label="Edit artifact bookmark"></wa-icon>
                    </wa-button>
                    <AppTooltip for="abv-edit-button">
                        {{ pendingCount > 0
                            ? `${pendingCount} network host${pendingCount > 1 ? 's' : ''} awaiting your decision`
                            : 'Edit artifact bookmark' }}
                    </AppTooltip>
                    <wa-button
                        v-if="isHtml"
                        id="abv-refresh-button"
                        size="small"
                        variant="neutral"
                        appearance="plain"
                        class="abv-title-btn reduced-height"
                        @click="reloadPreview"
                    >
                        <wa-icon name="arrows-rotate" label="Reload"></wa-icon>
                    </wa-button>
                    <AppTooltip v-if="isHtml" for="abv-refresh-button">Reload</AppTooltip>
                </div>
                <div class="abv-header__actions">
                    <wa-button
                        id="abv-share-button"
                        size="small"
                        :variant="shareVariant"
                        appearance="plain"
                        :class="['abv-share-button', 'reduced-height', {
                            'abv-share-button--active': activeShareCount > 0 && outdatedShareCount === 0,
                            'abv-share-button--attention': outdatedShareCount > 0 }]"
                        :disabled="!sharingEnabled"
                        @click="openShare"
                    >
                        <wa-icon name="share-nodes" label="Share"></wa-icon>
                    </wa-button>
                    <AppTooltip for="abv-share-button">
                        {{ !sharingEnabled
                            ? 'Configure a share host in Settings → Sharing to create links'
                            : outdatedShareCount > 0
                                ? `Share artifact — ${outdatedShareCount} update${outdatedShareCount > 1 ? 's' : ''} to push`
                                : activeShareCount > 0
                                    ? `Share artifact (${activeShareCount} active link${activeShareCount > 1 ? 's' : ''})`
                                    : 'Share artifact' }}
                    </AppTooltip>
                    <wa-button
                        id="abv-open-session-button"
                        size="small"
                        variant="neutral"
                        appearance="plain"
                        class="reduced-height"
                        @click="openInSession"
                    >
                        <wa-icon name="house" label="Open in session"></wa-icon>
                    </wa-button>
                    <AppTooltip for="abv-open-session-button">Open in session</AppTooltip>
                    <wa-button
                        id="abv-remove-button"
                        size="small"
                        variant="danger"
                        appearance="plain"
                        class="reduced-height"
                        @click="removeArtifactBookmark"
                    >
                        <wa-icon name="trash" label="Remove"></wa-icon>
                    </wa-button>
                    <AppTooltip for="abv-remove-button">Remove bookmark</AppTooltip>
                </div>
            </header>

            <!-- Missing file: lazy availability came back false -->
            <div v-if="available === false" class="abv-missing panel-card">
                <wa-callout variant="warning">
                    <wa-icon slot="icon" name="file-circle-exclamation"></wa-icon>
                    <strong>This artifact is no longer available.</strong>
                    <div>The file <code>{{ bookmark.relative_path }}</code> was moved, renamed, or deleted.</div>
                    <div class="abv-callout-actions">
                        <wa-button size="small" variant="danger" appearance="outlined" @click="removeArtifactBookmark">
                            <wa-icon slot="start" name="trash"></wa-icon>
                            Remove bookmark
                        </wa-button>
                        <wa-button size="small" variant="neutral" appearance="outlined" @click="openInSession">
                            <wa-icon slot="start" name="arrow-up-right-from-square"></wa-icon>
                            Open the session that created it
                        </wa-button>
                    </div>
                </wa-callout>
            </div>

            <!-- Render the artifact. Each bookmark gets its OWN FilePane
                 instance (keyed by id) inside a capped KeepAlive, so its
                 persistent-frame iframe survives a switch to another artifact
                 and back — no reload. The container stays mounted (v-show, not
                 v-else) even during a missing-artifact detour so that detour
                 doesn't evict the other cached previews; the FilePane itself is
                 v-if'd out then (deactivated, kept in cache). -->
            <div v-show="available !== false" class="abv-content panel-card">
                <KeepAlive :max="MAX_CACHED_ARTIFACTS">
                    <FilePane
                        v-if="available !== false"
                        :key="bookmark.id"
                        ref="filePaneRef"
                        :file-path="filePath"
                        :root-restriction="bookmark.root"
                        :preview-by-default="true"
                        :render-only="true"
                        :active="true"
                        api-prefix="/api"
                        :artifact-bookmark-session-id="bookmark.session_id"
                    />
                </KeepAlive>
            </div>
        </template>

        <!-- Edit dialog (reuses the create/edit dialog from the file viewer) -->
        <ArtifactBookmarkDialog
            v-if="bookmark"
            ref="editDialogRef"
            :session-id="bookmark.session_id"
            :relative-path="bookmark.relative_path"
            @removed="onRemovedFromDialog"
        />
    </div>
</template>

<style scoped>
.artifacts-browser {
    height: 100%;
    display: flex;
    flex-direction: column;
    min-height: 0;
    overflow: clip;
    overflow-clip-margin: var(--panel-gap);
}

.abv-empty {
    flex: 1;
    min-height: 0;
    overflow: auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-m);
    padding: var(--wa-space-xl);
}

.abv-empty__icon {
    font-size: 2rem;
    opacity: 0.5;
}

.abv-header {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-m);
    padding: 0 var(--wa-space-m) var(--wa-space-xs);
    min-width: 0;
}

:where(html.compact-height) .abv-header {
    padding-inline: var(--wa-space-xs);
}

.abv-header__title {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    min-width: 0;
    font-weight: var(--wa-font-weight-semibold);
}

.abv-title-btn {
    flex-shrink: 0;
}

.abv-header__name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
}

.abv-header__actions {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
}

/* Active share links → the button wears the brand colour (no count badge). */
.abv-share-button--active {
    opacity: 1;
    &::part(base) {
        color: var(--wa-color-brand-60);
    }
}
/* Something needs the owner's attention → warning tint + pulse (matches the
   pending-request indicators elsewhere). The share button uses it for updates to
   push; the edit button for network hosts awaiting an allow/deny decision. */
.abv-share-button--attention,
.abv-title-btn--attention {
    opacity: 1;
    animation: pending-pulse 1.5s ease-in-out infinite;
    &::part(base) {
        color: var(--wa-color-warning-60);
    }
}
@keyframes pending-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

.abv-content {
    flex: 1;
    min-height: 0;
    overflow: clip;
    display: flex;
    flex-direction: column;
}

.abv-missing {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: var(--wa-space-l);
}

.abv-callout-actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    margin-top: var(--wa-space-s);
}
</style>
