<script setup>
// Plan tab: a selector over every plan-like document the session touched
// (``Session.plan_paths`` — native Claude plan + pattern-detected docs, both
// providers) above a read-only rendered preview (FilePane in renderOnly mode:
// markdown/HTML render, no edit, no toolbar). Entries come newest-first from
// the store getter; relative paths resolve against the session's project
// directory, falling back to the ``worktree_of`` parent project so a doc
// written in a worktree stays reachable after the worktree is removed.
import { computed, ref, watch } from 'vue'
import { useDataStore } from '../../stores/data'
import FilePane from '../files/FilePane.vue'

const props = defineProps({
    sessionId: { type: String, required: true },
    projectId: { type: String, default: null },
    // True while the Plan tab is the shown tab in its region.
    active: { type: Boolean, default: false },
    // Doc selection from the URL (.../plan/<encoded stored path>), decoded by
    // SessionView. Bare /plan (null) means "newest document".
    routeDocPath: { type: String, default: null },
    // Whether this pane owns the URL (false for a docked, unfocused panel —
    // then routeDocPath belongs to another tab and must not drive selection).
    routeOwner: { type: Boolean, default: true },
    // Forwarded to FilePane: raise its pooled HTML-preview frame above the
    // docking overlay when this pane is shown there.
    frameElevated: { type: Boolean, default: false },
})

const emit = defineEmits(['navigate'])

const store = useDataStore()
const filePaneRef = ref(null)

const entries = computed(() => store.getSessionPlanDocs(props.sessionId))
const project = computed(() => (props.projectId ? store.projects[props.projectId] : null))
const parentProject = computed(() => {
    const parentId = project.value?.worktree_of
    return parentId ? store.projects[parentId] : null
})

// --- Selection (keyed by the stored entry path) ---------------------------

const selectedPath = ref(null)
const selectedEntry = computed(
    () => entries.value.find((e) => e.path === selectedPath.value) || null,
)

// Route → selection, gated on active + routeOwner like the Files/Git panels
// (a cached KeepAlive instance or a docked, unfocused panel must not read a
// URL that belongs to another session/tab). When the pane drives the URL: a
// tracked doc path wins (deep links, back/forward); a bare /plan re-asserts
// the newest entry (so Back from /plan/<doc> visibly resets); a doc path not
// tracked yet (deep link while the backfill runs) keeps the current selection
// silently — the watcher re-fires when the entry appears.
function syncSelection() {
    const list = entries.value
    if (props.active && props.routeOwner) {
        if (props.routeDocPath && list.some((e) => e.path === props.routeDocPath)) {
            selectedPath.value = props.routeDocPath
            return
        }
        if (!props.routeDocPath) {
            selectedPath.value = list[0]?.path ?? null
            return
        }
    }
    if (!list.some((e) => e.path === selectedPath.value)) {
        selectedPath.value = list[0]?.path ?? null
    }
}
watch(
    [entries, () => props.routeDocPath, () => props.routeOwner, () => props.active],
    syncSelection,
    { immediate: true },
)

watch(() => props.sessionId, () => { selectedPath.value = null; syncSelection() })

// wa-select treats spaces in values as multi-value separators, and doc paths
// may contain spaces: option values are URI-encoded, decoded back here.
// A user pick lands in the URL (like the Files/Git tabs), so precise plan
// links are bookmarkable; the route change loops back through syncSelection.
function onSelectChange(event) {
    const value = event.target.value
    const path = value ? decodeURIComponent(value) : null
    if (!path || path === selectedPath.value) return
    selectedPath.value = path
    emit('navigate', { docPath: path })
}

// --- Path resolution --------------------------------------------------------

function joinRoot(root, relative) {
    return `${root.replace(/\/+$/, '')}/${relative}`
}

function dirname(path) {
    const idx = path.lastIndexOf('/')
    return idx > 0 ? path.slice(0, idx) : '/'
}

// Resolved absolute path of the selected entry. Relative entries with both a
// project root and a worktree-parent root need a one-shot server probe (the
// client can't stat): try the project directory first, fall back to the
// parent's on failure. Single-root and absolute entries resolve synchronously.
// The roots are watch sources too — they may arrive after mount (projects
// still loading on a direct deep-link) and the resolution must re-run then.
const resolvedPath = ref(null)
let resolveToken = 0
// Already-probed two-root resolutions, keyed by stored entry path, so an
// updated_at bump (new entry object, same path) neither re-probes nor drops
// the mounted pane. Reset when the candidate roots change.
let probedByPath = new Map()

watch(
    [selectedEntry, () => project.value?.directory, () => parentProject.value?.directory],
    async ([entry, projectDir, parentDir], [, prevProjectDir, prevParentDir] = []) => {
        const token = ++resolveToken
        if (projectDir !== prevProjectDir || parentDir !== prevParentDir) probedByPath = new Map()
        if (!entry) {
            resolvedPath.value = null
            return
        }
        if (entry.path.startsWith('/')) {
            resolvedPath.value = entry.path
            return
        }
        const roots = [projectDir, parentDir].filter(Boolean)
        if (roots.length === 0) {
            resolvedPath.value = null
            return
        }
        const primary = joinRoot(roots[0], entry.path)
        if (roots.length === 1) {
            resolvedPath.value = primary
            return
        }
        const cached = probedByPath.get(entry.path)
        if (cached) {
            resolvedPath.value = cached
            return
        }
        // Keep the current pane mounted while probing (no pre-clear): the
        // updated_at-driven reload() stays effective and there is no flash.
        let resolved = primary
        try {
            const url = `/api/file-content/?path=${encodeURIComponent(primary)}&root=${encodeURIComponent(roots[0])}`
            const response = await fetch(url)
            if (!response.ok) resolved = joinRoot(roots[1], entry.path)
        } catch {
            // Network hiccup: keep the primary candidate, FilePane shows its own error.
        }
        if (token !== resolveToken) return
        probedByPath.set(entry.path, resolved)
        resolvedPath.value = resolved
    },
    { immediate: true },
)

// FilePane API routing: paths under the project (or its worktree parent) go
// through the session-scoped endpoints (validated by allowed_base_dirs, which
// whitelists both roots); anything else (native Claude plan, /tmp docs...)
// goes through the standalone endpoints confined to the file's directory.
const paneScope = computed(() => {
    const path = resolvedPath.value
    if (!path) return null
    const underRoot = (root) => !!root && path.startsWith(`${root.replace(/\/+$/, '')}/`)
    if (props.projectId && (underRoot(project.value?.directory) || underRoot(parentProject.value?.directory))) {
        return { projectId: props.projectId, sessionId: props.sessionId, apiPrefix: null, rootRestriction: null }
    }
    return { projectId: null, sessionId: null, apiPrefix: '/api', rootRestriction: dirname(path) }
})

// --- Labels -------------------------------------------------------------------

function entryLabel(entry) {
    const base = entry.source === 'claude_plan' ? 'Claude plan' : entry.path
    return entry.exists === false ? `${base} (missing)` : base
}

// --- Live refresh ----------------------------------------------------------------

// A store update bumping the selected entry's updated_at means the file
// changed on disk (new JSONL edit or plans-watcher tick): reload the pane.
watch(() => selectedEntry.value?.updated_at, (next, prev) => {
    if (next && prev && next !== prev) filePaneRef.value?.reload()
})

// --- On-demand existence re-probe -------------------------------------------

// An entry's ``exists`` flag is set at write-detection time from the
// ``tool_use`` line — logged before the tool flushes the file — so a doc
// written exactly once can stay flagged ``(missing)`` until the rare full
// recompute re-probes. When this tab is brought to the foreground (or its
// docs first arrive on a direct deep-link), ask the backend to re-probe: it
// persists + broadcasts ``session_updated`` only if a flag actually flipped.
// Watching ``entries.length`` (not the array itself) keeps updated_at bumps —
// which don't change the count — from re-firing during active editing; a short
// throttle guards against rapid tab toggles.
let lastProbeAt = 0
function refreshExistenceOnShow() {
    if (!props.active || !props.projectId || !entries.value.length) return
    const now = Date.now()
    if (now - lastProbeAt < 5000) return
    lastProbeAt = now
    store.refreshSessionPlanExistence(props.projectId, props.sessionId)
}
watch(
    [() => props.active, () => entries.value.length],
    refreshExistenceOnShow,
    { immediate: true },
)
// Re-arm the throttle when the session changes so the next tab is probed fresh.
watch(() => props.sessionId, () => { lastProbeAt = 0 })

// SessionView calls reload() on ``twicc:plan-changed`` and on WS reconnect.
defineExpose({ reload: () => filePaneRef.value?.reload() })
</script>

<template>
    <div class="plan-pane">
        <div class="plan-toolbar">
            <wa-select
                size="small"
                class="plan-select"
                :value="selectedPath ? encodeURIComponent(selectedPath) : ''"
                @change="onSelectChange"
            >
                <wa-option
                    v-for="entry in entries"
                    :key="entry.path"
                    :value="encodeURIComponent(entry.path)"
                >
                    <wa-icon
                        slot="start"
                        :name="entry.source === 'claude_plan' ? 'list-check' : 'file-lines'"
                    ></wa-icon>
                    {{ entryLabel(entry) }}
                </wa-option>
            </wa-select>
        </div>
        <div class="plan-content">
            <FilePane
                v-if="resolvedPath && paneScope"
                ref="filePaneRef"
                :key="resolvedPath"
                :file-path="resolvedPath"
                :project-id="paneScope.projectId"
                :session-id="paneScope.sessionId"
                :api-prefix="paneScope.apiPrefix"
                :root-restriction="paneScope.rootRestriction"
                :render-only="true"
                :preview-by-default="true"
                :active="active"
                :frame-elevated="frameElevated"
            />
            <div v-else class="plan-state">
                <wa-icon name="file-circle-question"></wa-icon>
                <span>{{ $t('session.plan.noDocument') }}</span>
            </div>
        </div>
    </div>
</template>

<style scoped>
.plan-pane {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
}
.plan-toolbar {
    flex: none;
    padding: var(--wa-space-2xs) var(--wa-space-s);
    border-bottom: 1px solid var(--wa-color-surface-border);
}
.plan-select {
    width: 100%;
}
.plan-content {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}
.plan-content > :deep(.file-pane) {
    flex: 1;
    min-height: 0;
}
.plan-state {
    display: flex;
    flex: 1;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
}
</style>
