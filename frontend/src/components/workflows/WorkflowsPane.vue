<script setup>
// A session's workflow runs: fetch the persisted Workflow rows (the 3-state view
// envelope) and render them as a tab bar — one tab per run, labelled with the
// title-cased workflow name + a status icon (spinner running, check/xmark done).
// The active tab's panel holds the run's structured detail (WorkflowRunDetail).
// Newest run active by default; a "View Workflow" navigation selects its tab; a
// run that appears live (a new tab) auto-activates, but only as it's added (a
// manual switch to another run isn't undone by later refetches).
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import WorkflowRunDetail from './WorkflowRunDetail.vue'
import TabBar from '../ui/TabBar.vue'
import { generateTemplates, sha256Hex, extractMeta } from '../../utils/workflowTemplates'
import { useWorkflowRunsStore } from '../../stores/workflowRuns'

const workflowRunsStore = useWorkflowRunsStore()

const props = defineProps({
    sessionId: { type: String, required: true },
    projectId: { type: String, required: true },
    // run_id to focus (select its tab), from a "View Workflow" navigation.
    focusRunId: { type: String, default: null },
    // True while the Workflows tab is the shown tab in its region.
    active: { type: Boolean, default: false },
})

// The active run to reflect in the URL — the owner performs the navigation (see syncUrl).
const emit = defineEmits(['navigate'])

const workflows = ref([])
const loading = ref(false)
const error = ref(null)
const hasLoaded = ref(false)
// run_id of the active tab (controlled wa-tab-group). Null before the first load.
const activeRunId = ref(null)

let controller = null
// `${run_id}:${scriptHash}` already generated + POSTed this component's life,
// so a STATE 0 run is synthesized at most once per script version (dedupe across
// the many load() triggers: mount, tab activation, live workflow_changed).
const synthesized = new Set()
// run_ids seen on the last successful load — to spot a *freshly added* run (a new
// tab) and auto-activate it, but only at the moment it appears (see load()).
let knownRunIds = new Set()

// "find-flaky-tests" → "Find Flaky Tests": dashes to spaces, capitalize each word.
function titleizeName(name) {
    if (!name) return ''
    return String(name)
        .split(/[-\s]+/)
        .filter(Boolean)
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
        .join(' ')
}

// Status shown by the tab's icon:
//   running     → spinner    (STATE 0/1 synthetic, or a real envelope mid-run)
//   completed   → check      terminal success
//   interrupted → stop       terminal non-success (e.g. a killed run)
// The back normalizes this into raw_json.statusKind (so the CLI reads the same
// value); we read it, falling back to the same by-exclusion rule for an envelope
// stored before the field existed. We never guess a "failed" token — see
// _run_status_kind in workflow_synthesis.py.
function statusKindOf(raw) {
    if (raw.statusKind) return raw.statusKind
    if (raw.synthetic) return 'running'
    const s = String(raw.status || '').toLowerCase()
    if (s === 'completed' || s === 'success' || s === 'done') return 'completed'
    return 'interrupted'
}

// Decorate each run with the fields the tab + panel need. Name comes from the
// envelope (workflowName); for a STATE 0 row not yet synthesized it's absent, so
// we read it straight from the launch script's meta literal — a correct first
// paint before STATE 1 lands.
const rows = computed(() => workflows.value.map((w) => {
    const raw = w.raw || {}
    let name = raw.workflowName
    if (name == null && typeof raw.script === 'string' && raw.script) {
        name = extractMeta(raw.script).name
    }
    return {
        run_id: w.run_id,
        raw,
        name: titleizeName(name),
        statusKind: statusKindOf(raw),
        // Cost lives in dedicated columns (not raw_json): total + {phaseIndex: cost}.
        cost: typeof w.cost === 'number' ? w.cost : null,
        phasesCost: w.phases_cost || {},
    }
}))

async function load() {
    if (controller) controller.abort()
    controller = new AbortController()
    loading.value = true
    error.value = null
    try {
        const url = `/api/projects/${encodeURIComponent(props.projectId)}`
            + `/sessions/${encodeURIComponent(props.sessionId)}/workflows/`
        const response = await fetch(url, { signal: controller.signal })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const data = await response.json()
        const ids = new Set(data.map(w => w.run_id))
        // Runs that appeared since the last successful load (newest-first in
        // `data`). Empty on the first load — we don't hijack the default/focus
        // selection then, only auto-activate genuinely new tabs that arrive live.
        const newlyAdded = hasLoaded.value ? data.filter(w => !knownRunIds.has(w.run_id)) : []
        workflows.value = data
        // Keep the active tab across live refetches when it's still present;
        // otherwise default to the newest run (data is newest-first).
        if (!activeRunId.value || !ids.has(activeRunId.value)) {
            activeRunId.value = data.length ? data[0].run_id : null
        }
        // A pending "View Workflow" navigation wins over the default.
        if (props.focusRunId && ids.has(props.focusRunId)) {
            activeRunId.value = props.focusRunId
        }
        // A freshly-appeared run becomes the active tab — but only at the moment
        // it's added (it's no longer "new" on later refetches, so a manual switch
        // to another run sticks). `activateRun` also syncs the URL, so the stale
        // focusRunId (still pointing at the previously-active run) can't revert it.
        if (newlyAdded.length) {
            activateRun(newlyAdded[0].run_id)
        }
        knownRunIds = ids
        hasLoaded.value = true
        maybeSynthesize(data)
    } catch (e) {
        if (e.name === 'AbortError') return
        error.value = e.message || 'Failed to load workflows'
    } finally {
        loading.value = false
    }
}

// A RUNNING run can't be phase-tagged on the back alone: building the templates
// the detector needs means *executing* the workflow script. When a run is STATE
// 0 (synthetic, no phases yet), generate {meta, templates} in the browser from
// its launch script and POST them — the back then builds the running view
// (STATE 1) and broadcasts, which refetches here. No-op for STATE 1/2.
async function maybeSynthesize(list) {
    for (const w of list) {
        const raw = w.raw
        if (!raw || !raw.synthetic) continue                          // STATE 2 (real) — done
        if (Array.isArray(raw.phases) && raw.phases.length) continue  // STATE 1 — already synthesized
        if (typeof raw.script !== 'string' || !raw.script) continue
        let hash
        try { hash = await sha256Hex(raw.script) } catch { continue }
        const key = `${w.run_id}:${hash}`
        if (synthesized.has(key)) continue
        synthesized.add(key)
        // generateTemplates never throws: on a script it can't execute it returns
        // failed:true with empty templates. We POST anyway (meta + the flag) so the
        // back builds a degraded view + flags detection unavailable, rather than
        // leaving a mute STATE 0. The hash dedup stops it retrying until the script
        // changes (a deterministic failure won't fix itself on retry).
        const { meta, templates, failed } = await generateTemplates(raw.script, { runs: 100 })
        if (failed) console.error('[workflow] template generation failed for', w.run_id)
        await postSynthesis(w.run_id, meta, templates, hash, key, failed)
    }
}

async function postSynthesis(runId, meta, templates, scriptHash, key, detectionUnavailable = false) {
    const url = `/api/projects/${encodeURIComponent(props.projectId)}`
        + `/sessions/${encodeURIComponent(props.sessionId)}`
        + `/workflows/${encodeURIComponent(runId)}/synthesis/`
    let res
    try {
        res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ meta, templates, script_hash: scriptHash, detection_unavailable: detectionUnavailable }),
        })
    } catch {
        synthesized.delete(key)   // transient network error — let a later load() retry
        return
    }
    // 5xx → transient, allow retry. 4xx (stale hash / completed / bad input):
    // a changed script re-triggers under a new hash, a done run needs nothing —
    // keep the guard. On success the back broadcasts workflow_changed, which
    // refetches and renders STATE 1; no explicit reload needed here.
    if (!res.ok && res.status >= 500) synthesized.delete(key)
}

// User switched tabs. wa-tab-group also (re)emits wa-tab-show on (re)mount for
// the already-active tab — forward only real changes (re-affirmation guard, which
// also keeps programmatic :active changes from re-writing the URL). The template
// uses @wa-tab-show.stop so this never reaches DockRegion's own wa-tab-group (our
// pane lives inside one of its tabs).
function onTabShow(event) {
    const name = event.detail?.name
    if (!name || name === activeRunId.value) return
    activeRunId.value = name
    syncUrl(name)
}

// URL-drive the tabs: reflect the active run in the address bar so a tab click is
// a real navigation (replace → no history spam). The route's runId param flows
// back as focusRunId, keeping clicks and external "View Workflow" links in sync.
// No-op when the URL already targets this run.
//
// The navigation itself is delegated to the owner (like every other tool pane):
// it holds the frame — current sidebar project filter + ?workspace= — which this
// pane has no business rewriting. Building the location here from `props.projectId`
// (the session's OWN project) used to switch the sidebar to it and drop the
// workspace whenever the session was being viewed cross-filter.
function syncUrl(runId) {
    if (props.focusRunId === runId) return
    emit('navigate', { runId })
}

// Select the targeted run's tab (from a "View Workflow" click). If it isn't in
// the loaded list yet — a brand-new run whose row just appeared — refetch; load()
// activates it once present (the live workflow_changed event also refetches).
function focusRun(runId) {
    if (!runId) return
    if (hasLoaded.value && !workflows.value.some(w => w.run_id === runId)) {
        load()
        return
    }
    activeRunId.value = runId
}

// Live refresh: the watcher broadcasts workflow_changed when a wf_*.json is
// created/updated; debounce bursts (the engine rewrites the file each tick).
let reloadTimer = null
function onWorkflowChanged(event) {
    if (event.detail?.sessionId !== props.sessionId) return
    clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => { if (hasLoaded.value) load() }, 400)
}

// Exposed so SessionView can refetch us on a WebSocket reconnection (a
// workflow_changed broadcast may have been dropped while the socket was down,
// e.g. a run completing → STATE 2). Same mechanism as PlanPane/FilesPanel —
// SessionView is the stable listener; a per-pane window listener is unreliable.
defineExpose({ reload: load })

// ─── Keyboard shortcuts: workflow run-tab navigation (Alt+Ctrl+Shift+{1-9, ←/→, ↑/↓})
// Events dispatched by App.vue, handled here by the active instance only — the
// same system as the terminal tabs. Activation is URL-driven (like a tab click),
// so we set activeRunId and sync the URL together.
const workflowTabHistory = []
const MAX_WORKFLOW_TAB_HISTORY = 50

function pushWorkflowTabHistory(runId) {
    if (runId == null) return
    if (workflowTabHistory.length > 0 && workflowTabHistory[workflowTabHistory.length - 1] === runId) return
    workflowTabHistory.push(runId)
    if (workflowTabHistory.length > MAX_WORKFLOW_TAB_HISTORY) workflowTabHistory.shift()
}

// Track tab transitions for the last-visited jump — only while visible, and the
// leaving run (Alt+Tab-like), matching the terminal pane.
watch(activeRunId, (newId, oldId) => {
    if (!props.active) return
    if (oldId != null && oldId !== newId) pushWorkflowTabHistory(oldId)
})

function activateRun(runId) {
    activeRunId.value = runId
    syncUrl(runId)
}

function handleWorkflowTabShortcut(event) {
    if (!props.active) return
    const list = rows.value
    if (!list.length) return
    const { type, index } = event.detail

    if (type === 'direct') {
        // Number N → the Nth run tab (1-based positional).
        const row = list[index - 1]
        if (row) activateRun(row.run_id)
    } else if (type === 'prev' || type === 'next') {
        const currentIdx = list.findIndex(r => r.run_id === activeRunId.value)
        if (currentIdx === -1) return
        const newIdx = type === 'next'
            ? (currentIdx + 1) % list.length
            : (currentIdx - 1 + list.length) % list.length
        activateRun(list[newIdx].run_id)
    } else if (type === 'last-visited') {
        const validIds = new Set(list.map(r => r.run_id))
        for (let i = workflowTabHistory.length - 1; i >= 0; i--) {
            const id = workflowTabHistory[i]
            if (id !== activeRunId.value && validIds.has(id)) {
                activateRun(id)
                return
            }
        }
    }
}

// Mirror runs + active tab into a store so the command palette can list and
// navigate them (it has no access to this component's local state).
watch(rows, (r) => {
    workflowRunsStore.setRuns(props.sessionId, r.map(row => ({ run_id: row.run_id, name: row.name })))
}, { immediate: true })
watch(activeRunId, (id) => workflowRunsStore.setActive(props.sessionId, id), { immediate: true })

onMounted(() => {
    load()
    window.addEventListener('twicc:workflow-changed', onWorkflowChanged)
    window.addEventListener('twicc:workflow-tab-shortcut', handleWorkflowTabShortcut)
})
watch(() => props.active, (active) => { if (active) load() })
watch(() => props.sessionId, (newId, oldId) => {
    if (oldId) workflowRunsStore.clear(oldId)
    hasLoaded.value = false; synthesized.clear(); knownRunIds = new Set(); load()
})
watch(() => props.focusRunId, (runId) => { if (runId && hasLoaded.value) focusRun(runId) })
onBeforeUnmount(() => {
    window.removeEventListener('twicc:workflow-changed', onWorkflowChanged)
    window.removeEventListener('twicc:workflow-tab-shortcut', handleWorkflowTabShortcut)
    workflowRunsStore.clear(props.sessionId)
    clearTimeout(reloadTimer)
    if (controller) controller.abort()
})
</script>

<template>
    <div class="workflows-pane">
        <div v-if="error" class="workflows-state workflows-error">
            <wa-icon name="triangle-exclamation"></wa-icon>
            <span>{{ error }}</span>
        </div>
        <div v-else-if="loading && !hasLoaded" class="workflows-state">{{ $t('workflows.loading') }}</div>
        <div v-else-if="!rows.length" class="workflows-state">{{ $t('workflows.empty') }}</div>
        <TabBar v-else class="workflows-tabs" :active="activeRunId" @wa-tab-show.stop="onTabShow">
            <wa-tab v-for="row in rows" :key="row.run_id" slot="nav" :panel="row.run_id" class="workflow-tab">
                <span class="workflow-tab-name">{{ row.name }}</span>
                <wa-spinner v-if="row.statusKind === 'running'" class="workflow-tab-status"></wa-spinner>
                <wa-icon v-else-if="row.statusKind === 'interrupted'" name="circle-stop" class="workflow-tab-status workflow-tab-status-interrupted"></wa-icon>
                <wa-icon v-else name="circle-check" class="workflow-tab-status workflow-tab-status-done"></wa-icon>
            </wa-tab>
            <wa-tab-panel v-for="row in rows" :key="row.run_id" :name="row.run_id" class="workflow-panel">
                <WorkflowRunDetail :raw="row.raw" :status-kind="row.statusKind" :cost="row.cost" :phases-cost="row.phasesCost" :project-id="projectId" :session-id="sessionId" />
            </wa-tab-panel>
        </TabBar>
    </div>
</template>

<style scoped>
.workflows-pane {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
}

.workflows-state {
    color: var(--wa-color-text-quiet, #8b97a7);
    padding: var(--wa-space-l, 1.5rem);
    display: flex;
    align-items: center;
    gap: var(--wa-space-s, 0.5rem);
}

.workflows-error {
    color: var(--wa-color-danger-fill-loud, #d73a49);
}

/* One tab per run; the active panel holds its structured detail. Make the
 * tab-group fill the pane and scroll its body (not the whole pane), so the tab
 * bar stays put while a long run detail scrolls. */
.workflows-tabs {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.workflows-tabs::part(base) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.workflows-tabs::part(body) {
    flex: 1;
    min-height: 0;
    overflow: auto;
}

/* Compact tab bar: the trimmed tab padding + thin track come from TabBar; here we
 * only shrink the label font (like the terminal bar) and drop the nav's bottom
 * padding, scoped to the tabs so the panel content keeps its own sizing. */
.workflows-tabs::part(nav) {
    padding-bottom: 0;
}

.workflow-tab {
    font-size: var(--wa-font-size-s);
}

.workflow-tab-status {
    font-size: 1.1em;
}

.workflow-tab-status-done {
    color: var(--wa-color-success-50);
}

.workflow-tab-status-interrupted {
    color: var(--wa-color-warning-50);
}

.workflow-panel {
    --padding: var(--wa-space-m);
}
</style>
