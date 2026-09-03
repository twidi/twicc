<script setup>
// Browser pane: embeds a user-chosen URL (typically the project's dev server)
// in a plain iframe. Unlike the Artifacts preview there is NO sandbox, NO CSP
// lockdown and NO network broker — deliberate: the page runs exactly as in a
// normal browser tab (direct network, service workers, HMR websockets), it
// just cannot reach into TwiCC (cross-origin isolation applies both ways).
//
// Cross-origin iframes expose neither their current location nor their
// history. Two modes:
// - Companion mode: the embedded page includes the TwiCC companion script
//   (served at /_twicc/browser-companion.js) and reports real navigation over
//   postMessage; Back/Forward/Refresh drive the page's own history.
// - Fallback: the toolbar keeps its OWN history stack of URLs navigated via
//   the address bar / Back / Forward / Home only; links followed inside the
//   page are invisible and Refresh re-creates the iframe on the last recorded
//   URL. Deliberate, documented limits — see the info tooltip.
//
// The iframe itself is rendered through PersistentFrame (frames/), so it lives
// in the app-level FrameHost and is NOT reloaded by session switches (KeepAlive)
// or dock moves — only an explicit remount (frameKey bump on hard navigation /
// refresh in fallback mode) re-creates it.
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { hostMessage, isCompanionMessage } from '../../browser-companion/protocol'
import { isOpen as mediaPreviewOpen } from '../../composables/useMediaPreview'
import { useCommandRegistry } from '../../composables/useCommandRegistry'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { useWorkspacesStore } from '../../stores/workspaces'
import { apiFetch } from '../../utils/api'
import { resolveProjectBrowserUrl } from '../../utils/browserDefaults'
import { normalizeBrowserUrl } from '../../utils/browserUrl'
import { usablePublicOrigin } from '../../utils/publicOrigin'
import {
    addBrowserUrlEntry,
    defaultBrowserUrlEntry,
    removeBrowserUrlEntry,
    setDefaultBrowserUrlEntry,
} from '../../utils/browserUrlEntries'
import { debounce } from '../../utils/debounce'
import { showHelp } from '../help/showHelp'
import PersistentFrame from '../frames/PersistentFrame.vue'
import SelectAreaToolbar from '../frames/SelectAreaToolbar.vue'
import ViewportStage from '../frames/ViewportStage.vue'
import ViewportToolbar from '../frames/ViewportToolbar.vue'
import ProjectBadge from '../project/ProjectBadge.vue'
import WorktreeBadge from '../project/WorktreeBadge.vue'
import TextSelectionComment from '../session/detail/TextSelectionComment.vue'
import AppTooltip from '../ui/AppTooltip.vue'

// Stable identity — an inline literal would churn the frame descriptor watch.
const BROWSER_FRAME_ATTRS = { allow: 'clipboard-read; clipboard-write; fullscreen', title: 'Browser' }

const props = defineProps({
    sessionId: { type: String, default: null },
    projectId: { type: String, default: null },
    // True while the Browser tab is the shown tab in its region — drives the
    // lazy first load (never fetch a dev server for a tab that was never opened).
    active: { type: Boolean, default: false },
    // Bumped by SessionView on explicit tab activation → focus the address bar.
    focusRequest: { type: Number, default: 0 },
    // True while this pane's frame is shown inside the docking overlay — raises
    // the pooled iframe above the overlay panel.
    frameElevated: { type: Boolean, default: false },
})

// 'interact': a real user interaction inside the embedded page (companion
// 'focus' message) — SessionView treats it like a click in the pane body and
// claims the route for the Browser tab.
const emit = defineEmits(['interact'])

const store = useDataStore()
const settingsStore = useSettingsStore()
const workspacesStore = useWorkspacesStore()
const instanceId = useId()

// Palette commands, registered further down once their state exists. Ids are
// per-instance: several panes can be mounted at once (one per cached session).
const { registerCommand, unregisterCommand } = useCommandRegistry()
const openSavedUrlCommandId = `nav.browser-open-url.${instanceId}`
const saveUrlCommandId = `ui.browser-save-url.${instanceId}`

// ── Default URL: project chain first, then the first non-archived workspace
// containing the project (worktree-aware) that carries saved URLs.
const projectDefaultUrl = computed(() => resolveProjectBrowserUrl(props.projectId, store.projects))
const workspaceDefaultUrl = computed(() => {
    if (!props.projectId) return null
    const ws = workspacesStore.workspaces.find(
        (w) => !w.archived && w.browserUrls?.length && workspacesStore.workspaceContainsProject(w.id, props.projectId)
    )
    return defaultBrowserUrlEntry(ws?.browserUrls)?.url || null
})
const defaultUrl = computed(() => projectDefaultUrl.value || workspaceDefaultUrl.value)

// ── Navigation state. `urlHistory` holds address-bar-level navigations only
// (fallback mode; named to avoid shadowing window.history in this component).
// `currentUrl` is the pane's display/persist state; the iframe binds the
// SEPARATE `frameSrc` — binding :src to the live currentUrl would re-navigate
// the frame every time the companion reports an in-page URL change.
const currentUrl = ref('')       // URL currently shown ('' = blank state)
const inputUrl = ref('')         // address bar edit buffer
const frameSrc = ref('')         // what the iframe element was last pointed at
const urlHistory = ref([])
const historyIndex = ref(-1)
const frameKey = ref(0)          // bump = recreate the iframe (fallback navigate / refresh)
const loading = ref(false)
const everActivated = ref(false)
// The PersistentFrame component; frameEl resolves to the live <iframe> element
// it manages (in the pool, or inline in fallback contexts). Companion messaging
// reads frameEl.value.contentWindow through it.
const persistentFrameRef = ref(null)
const frameEl = computed(() => persistentFrameRef.value?.frameEl ?? null)
const addressFocused = ref(false)

// ── Companion channel. The embedded page may include the TwiCC companion
// script; presence is a tiny state machine driven by its hello/bye messages
// and the iframe load event:
//   absent  — determined: no companion in the current document
//   waiting — undetermined: navigation in flight / grace period running
//   present — handshake done; navigation flows both ways
const companionStatus = ref('absent')
// Companion-mode Back/Forward history. The HOST owns it: it lives here, in the
// TwiCC parent, so it survives full reloads of the embedded frame — MPA and
// cross-origin navigations included, unlike anything the companion could keep.
// Fed by the companion's URL reports; each Back/Forward resolves to a target URL
// the companion then moves to (soft in-document, or a reload). Independent of
// the fallback `urlHistory`, and never reset by companion connect/disconnect.
const navStack = ref([])
const navIndex = ref(-1)
const NAV_STACK_MAX = 200
let companionOrigin = null
let helloGraceTimer = null
// True once a companion connected: the fallback stack froze during that
// session, so it must be reset when the pane later degrades to fallback mode.
let hadCompanion = false
const HELLO_GRACE_MS = 3000

function sendToCompanion(message) {
    if (!frameEl.value?.contentWindow || !companionOrigin) return
    frameEl.value.contentWindow.postMessage(message, companionOrigin)
}

function clearHelloGraceTimer() {
    if (helloGraceTimer) {
        clearTimeout(helloGraceTimer)
        helloGraceTimer = null
    }
}

function onWindowMessage(event) {
    // Only our own frame's current document, nothing else (other panes, other
    // windows, devtools). contentWindow is re-read live: a frameKey remount
    // invalidates messages from the torn-down document by identity.
    if (!frameEl.value || event.source !== frameEl.value.contentWindow) return
    const data = event.data
    if (!isCompanionMessage(data)) return
    if (data.type === 'hello') {
        companionOrigin = event.origin
        companionStatus.value = 'present'
        hadCompanion = true
        // A hello means a fresh document — any select-area overlay died with
        // the old one, so the host flag must not claim the mode is still on.
        selectAreaActive.value = false
        selectState.value = null
        // Fresh document → the previous page's errors are gone.
        pageErrors.value = []
        pageErrorTotal.value = 0
        rejectPendingCapture('page reloaded')
        clearHelloGraceTimer()
        // A connected companion is definitive proof the page is reachable and
        // embeddable — drop any banner a slower/raced probe put up.
        probeResult.value = null
        event.source.postMessage(hostMessage('ack'), event.origin)
        return
    }
    if (event.origin !== companionOrigin) return
    if (data.type === 'state') {
        // The embedded page is untrusted: only adopt plain http(s) URLs. A
        // hostile page could report a javascript:/data: string that would
        // otherwise reach the UNsandboxed iframe src on a later fallback
        // navigation — i.e. script execution in TwiCC's own origin. The
        // scheme is lowercased so case-sensitive downstream checks (backend
        // validate_browser_url, mixedContentBlocked) can't be sidestepped.
        const url =
            typeof data.url === 'string' && /^https?:\/\//i.test(data.url)
                ? data.url.replace(/^https?/i, (scheme) => scheme.toLowerCase())
                : null
        if (url) {
            reconcileNav(url)
            if (url !== currentUrl.value) {
                currentUrl.value = url
                // Never clobber an in-progress address-bar edit.
                if (!addressFocused.value) inputUrl.value = url
                probeResult.value = null // a page just loaded — any diagnosis is stale
                persistUrlDebounced()
            }
        }
    } else if (data.type === 'select-state') {
        // Capabilities of the currently highlighted element (select-area
        // mode) — drives the select toolbar's navigation buttons. Ignored
        // when the mode is off (a late message from a just-disabled overlay).
        if (selectAreaActive.value) {
            selectState.value = {
                hasSelection: data.hasSelection === true,
                locked: data.locked === true,
                canParent: data.canParent === true,
                canFirstChild: data.canFirstChild === true,
                canPrevSibling: data.canPrevSibling === true,
                canNextSibling: data.canNextSibling === true,
            }
        }
    } else if (data.type === 'select-describe') {
        // The companion described the highlighted element (opening tag + text
        // + ancestor chain) — open the comment widget on it.
        if (selectAreaActive.value && typeof data.chain === 'string') {
            const lines = []
            if (typeof data.openingTag === 'string' && data.openingTag) lines.push(`Element: ${data.openingTag}`)
            if (typeof data.text === 'string' && data.text) lines.push(`Text: "${data.text}"`)
            lines.push(`Path: ${data.chain}`)
            openSelectComment(lines.join('\n'))
        }
    } else if (data.type === 'select-capture') {
        // Answer to a capture request (see captureScreenshot): resolve/reject
        // the in-flight promise; the comment widget renders the result. A late
        // or duplicate reply with no pending request is ignored.
        if (pendingCapture) {
            const { resolve, reject, timer } = pendingCapture
            pendingCapture = null
            clearTimeout(timer)
            if (typeof data.error === 'string') reject(new Error(data.error))
            else if (typeof data.dataUrl === 'string' && data.dataUrl.startsWith('data:image/')) resolve(data.dataUrl)
            else reject(new Error('no image returned'))
        }
    } else if (data.type === 'errors') {
        // The companion pushed the current page-error buffer (console.error,
        // uncaught exceptions, unhandled rejections). Drives the toolbar badge.
        if (Array.isArray(data.errors)) {
            pageErrors.value = data.errors
                .filter((e) => e && typeof e.text === 'string')
                .map((e) => ({ kind: typeof e.kind === 'string' ? e.kind : 'error', text: e.text }))
            pageErrorTotal.value = typeof data.total === 'number' ? data.total : pageErrors.value.length
        }
    } else if (data.type === 'focus') {
        // Real user input inside the embedded page — the cross-origin
        // equivalent of DockRegion's click-to-focus. A hidden frame can't
        // receive input (visibility:hidden + pointer-events:none), but guard
        // on `active` anyway against stale messages mid-teardown.
        if (props.active) emit('interact')
    } else if (data.type === 'bye') {
        // Document going away (navigation / reload). Undetermined until the
        // next document says hello or the post-load grace period expires.
        companionStatus.value = 'waiting'
        selectAreaActive.value = false
        selectState.value = null
        pageErrors.value = []
        pageErrorTotal.value = 0
        rejectPendingCapture('page navigating away')
        // The frozen pre-companion stack becomes reachable the moment we
        // leave 'present' — and this waiting window is exactly when users
        // click Back ("page is taking too long"). Reset it NOW; clearing
        // hadCompanion makes the absent-transition reset a pure lost-bye
        // safety net (it must not re-wipe valid toolbar entries the user
        // accumulates during the waiting window).
        hadCompanion = false
        urlHistory.value = currentUrl.value ? [currentUrl.value] : []
        historyIndex.value = urlHistory.value.length - 1
    }
}

// ── Full-window: the WHOLE pane (toolbar + frame) expands in place via a
// position:fixed class — the same mechanism as FilePane's preview fullscreen.
// The toolbar stays on top and fully usable (Back/Forward, address, companion)
// while the embedded page fills the window. Never teleported: moving the iframe
// would reload it; the pooled frame just follows its placeholder's new rect and
// switches to the 'fullscreen' z-tier so it layers above the fixed pane.
const isFullscreen = ref(false)
const fullscreenButtonId = `browser-fullscreen-${instanceId}`
// Provided by ProjectView: drops .main-content's container-type and lifts it
// above the sidebar/divider so the fixed overlay covers the whole window.
// Absent outside a project view → graceful no-op (overlay clamps to the pane).
const expandPreviewHost = inject('expandPreviewHost', null)

function toggleFullscreen() {
    isFullscreen.value = !isFullscreen.value
}

// Escape exits full-window. Capture phase + stopPropagation so it unwraps here
// before any ancestor Escape handler reacts.
function onFullscreenKeydown(event) {
    if (event.key === 'Escape') {
        // An open select-comment widget or media preview has priority: this
        // capture-phase listener would otherwise exit fullscreen on the same
        // keypress those use to close themselves.
        if (commentPosition.value || mediaPreviewOpen.value) return
        event.stopPropagation()
        isFullscreen.value = false
    }
}

watch(isFullscreen, (on) => {
    expandPreviewHost?.(on)
    if (on) window.addEventListener('keydown', onFullscreenKeydown, true)
    else window.removeEventListener('keydown', onFullscreenKeydown, true)
})

onMounted(() => window.addEventListener('message', onWindowMessage))
onBeforeUnmount(() => {
    window.removeEventListener('message', onWindowMessage)
    window.removeEventListener('keydown', onFullscreenKeydown, true)
    unregisterCommand(openSavedUrlCommandId)
    unregisterCommand(saveUrlCommandId)
    if (isFullscreen.value) expandPreviewHost?.(false)
    clearHelloGraceTimer()
    rejectPendingCapture('pane closed')
    persistUrlDebounced.cancel()
})

const canGoBack = computed(() =>
    companionStatus.value === 'present' ? navIndex.value > 0 : historyIndex.value > 0
)
const canGoForward = computed(() =>
    companionStatus.value === 'present'
        ? navIndex.value < navStack.value.length - 1
        : historyIndex.value < urlHistory.value.length - 1
)

// ── Per-session persistence: restore the last URL across page reloads.
// Read once at first activation (it wins over the defaults); written back
// debounced on each navigation — toolbar-initiated or companion-reported.
// Drafts have no backend row — their URL stays transient. The pane state
// lives in the refs above, never on the store's session object, so the
// session_updated echo can't clobber it.
const BROWSER_URL_PERSIST_DEBOUNCE_MS = 1000
let lastPersistedUrl = null
const persistUrlDebounced = debounce(async () => {
    const sessionRow = store.getSession(props.sessionId)
    if (!sessionRow || sessionRow.draft) return
    const url = currentUrl.value
    if (url === lastPersistedUrl) return
    try {
        const response = await apiFetch(`/api/projects/${props.projectId}/sessions/${props.sessionId}/`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ browser_url: url }),
        })
        if (response.ok) lastPersistedUrl = url
    } catch {
        // Transient UI state — losing a write is acceptable.
    }
}, BROWSER_URL_PERSIST_DEBOUNCE_MS)

// An https TwiCC page cannot embed an http iframe (the browser blocks it
// silently as mixed content) — explain instead of showing a dead frame.
const mixedContentBlocked = computed(
    () => window.location.protocol === 'https:' && currentUrl.value.startsWith('http://')
)

// Fold a companion-reported URL into the host-owned history. Idempotent on the
// current entry; a neighbour match is a traversal (move the cursor); a known
// entry elsewhere is a jump; anything else is a new forward navigation (which
// truncates the forward entries). It only ever sees URLs, so it treats a soft
// in-document move and a full document load exactly the same.
function reconcileNav(url) {
    const s = navStack.value
    const i = navIndex.value
    if (s[i] === url) return
    if (s[i + 1] === url) return void (navIndex.value = i + 1)
    if (s[i - 1] === url) return void (navIndex.value = i - 1)
    const existing = s.indexOf(url)
    if (existing !== -1) return void (navIndex.value = existing)
    const next = s.slice(0, i + 1)
    next.push(url)
    if (next.length > NAV_STACK_MAX) next.shift()
    navStack.value = next
    navIndex.value = next.length - 1
}

// Fallback-mode hard navigation: point the (re-created) iframe at a URL.
function showFrame(url) {
    currentUrl.value = url
    inputUrl.value = url
    frameSrc.value = url
    frameKey.value++
    loading.value = true
    companionStatus.value = 'waiting'
    clearHelloGraceTimer()
    probeResult.value = null // clear a stale diagnosis right away
    probeCurrentUrl()
    persistUrlDebounced()
}

function navigate(rawInput) {
    const url = normalizeBrowserUrl(rawInput)
    if (!url) return
    if (companionStatus.value === 'present') {
        // Address-bar navigation: load it in the frame (the companion assigns
        // it). The new page reports its URL back and reconcileNav appends it to
        // the host history — no shared-history traversal, parent untouched.
        sendToCompanion(hostMessage('command', { action: 'navigate', url }))
        currentUrl.value = url
        inputUrl.value = url
        loading.value = true
        probeResult.value = null
        persistUrlDebounced()
        return
    }
    // Fallback: truncate forward entries, then push (skip contiguous repeats).
    const stack = urlHistory.value.slice(0, historyIndex.value + 1)
    if (stack[stack.length - 1] !== url) stack.push(url)
    urlHistory.value = stack
    historyIndex.value = stack.length - 1
    showFrame(url)
}

function goBack() {
    if (companionStatus.value === 'present') {
        if (navIndex.value <= 0) return
        navIndex.value--
        // The companion moves to the target — softly if it's in the current
        // document, else a reload. Never a shared-history traversal, so the
        // TwiCC parent is never dragged along.
        sendToCompanion(hostMessage('command', { action: 'navigate-to', url: navStack.value[navIndex.value] }))
        loading.value = true
        return
    }
    if (!canGoBack.value) return
    historyIndex.value--
    showFrame(urlHistory.value[historyIndex.value])
}

function goForward() {
    if (companionStatus.value === 'present') {
        if (navIndex.value >= navStack.value.length - 1) return
        navIndex.value++
        sendToCompanion(hostMessage('command', { action: 'navigate-to', url: navStack.value[navIndex.value] }))
        loading.value = true
        return
    }
    if (!canGoForward.value) return
    historyIndex.value++
    showFrame(urlHistory.value[historyIndex.value])
}

function refresh() {
    if (!currentUrl.value) return
    if (companionStatus.value === 'present') {
        // In-place reload — keeps in-page navigation and session history.
        sendToCompanion(hostMessage('command', { action: 'reload' }))
        loading.value = true
        return
    }
    showFrame(currentUrl.value)
}

function goHome() {
    if (primaryHomeUrl.value) navigate(primaryHomeUrl.value)
}

function openExternal() {
    if (currentUrl.value) window.open(currentUrl.value, '_blank', 'noopener')
}

function onAddressSubmit() {
    navigate(inputUrl.value)
}

function onFrameLoad() {
    // Fires even for framing-refused pages (the error document loads) — it
    // only means "network settled", not "content visible".
    loading.value = false
    // Presence: a companion's hello normally arrives BEFORE load (deferred
    // scripts run first). If it hasn't by now, give slow injectors a short
    // grace, then declare absence. The probe re-runs on absence so a dead
    // server is diagnosed even on companion-mode navigations (which skip
    // showFrame and its probe call).
    // Staying 'present' here trusts that a cross-document navigation reset us
    // to 'waiting' via the old document's bye first (reliable for normal
    // in-frame navigations). We can't observe load-start on a cross-origin
    // frame, so a genuinely lost bye is the one case this can't self-heal —
    // an accepted limit of the cross-origin model (recovered by a TwiCC reload).
    if (companionStatus.value === 'present') return
    clearHelloGraceTimer()
    helloGraceTimer = setTimeout(() => {
        helloGraceTimer = null
        companionStatus.value = 'absent'
        if (hadCompanion) {
            // The fallback stack froze during the companion session — its
            // entries predate it, and Back would jump to a long-gone page.
            // Restart from the current URL: an honest degraded state.
            hadCompanion = false
            urlHistory.value = currentUrl.value ? [currentUrl.value] : []
            historyIndex.value = urlHistory.value.length - 1
        }
        probeCurrentUrl()
    }, HELLO_GRACE_MS)
}

// ── Advisory probe: the two silent-blank-frame cases (server down, framing
// refused) are invisible client-side; ask the backend. Failures are ignored —
// the endpoint is advisory.
// probeResult is CLEARED at navigation points (showFrame, companion navigate,
// companion-reported page load) and only ASSIGNED here — never reset at probe
// start. Two probes can race on one navigation (showFrame's immediate one and
// the post-load absence one); both converge on the same value for the same
// URL, so a late response can't flicker an already-displayed banner away.
const probeResult = ref(null)

async function probeCurrentUrl() {
    const url = currentUrl.value
    if (!url) return
    try {
        const response = await apiFetch(`/api/browser-frame-check/?url=${encodeURIComponent(url)}`)
        if (!response.ok) return
        const data = await response.json()
        if (currentUrl.value !== url) return // user navigated meanwhile
        probeResult.value = data.reachable === false || data.embeddable === false ? data : null
    } catch {
        // advisory only
    }
}

// ── Lazy init: on first activation, auto-load the session's persisted URL
// (survives page reloads) or, failing that, the resolved default.
watch(
    () => props.active,
    (active) => {
        if (!active || everActivated.value) return
        everActivated.value = true
        const saved = store.getSession(props.sessionId)?.browser_url || null
        lastPersistedUrl = saved
        const initial = saved || defaultUrl.value
        if (initial) navigate(initial)
    },
    { immediate: true }
)

// ── Focus the address bar on explicit tab activation (keyboard / tab click),
// mirroring the other ACTIVATION_FOCUS_TABS panels.
const addressInputRef = ref(null)
watch(
    () => props.focusRequest,
    () => {
        requestAnimationFrame(() => addressInputRef.value?.focus())
    }
)

// ── Companion hint: snippet banner + status icon --------------------------
const snippetDismissed = ref(false)
const snippetCopied = ref(false)
// Prefer the configured External address (publicBaseUrl) so the snippet stays
// reachable from wherever the dev page runs — a `localhost` src only resolves
// on this machine. Falls back to the current origin when no External address is set.
const companionSnippet = computed(() => {
    const origin = usablePublicOrigin(settingsStore.getPublicBaseUrl) || window.location.origin
    return `<script src="${origin}/_twicc/browser-companion.js" defer><\/script>`
})
// Nag on any loaded page without a companion, unless the user dismissed it
// (dismissal is per-mount — a TwiCC reload brings it back). Still yields to a
// higher-priority diagnostic (probe / mixed-content banner) and never shows on
// the blank state. Not gated on URL locality: a tunnel / reverse-proxy dev page
// is one the user owns just as much as localhost — the plug already treats every
// page as companion-eligible, so the banner matches it.
const showCompanionHint = computed(
    () =>
        companionStatus.value === 'absent' &&
        !!currentUrl.value &&
        !snippetDismissed.value &&
        !probeResult.value &&
        !mixedContentBlocked.value
)

async function copySnippet() {
    try {
        await navigator.clipboard.writeText(companionSnippet.value)
        snippetCopied.value = true
        setTimeout(() => {
            snippetCopied.value = false
        }, 1500)
    } catch {
        // Clipboard unavailable (permissions) — the snippet is selectable text.
    }
}

const companionTooltip = computed(() => {
    if (companionStatus.value === 'present') {
        return 'Companion connected — real navigation history, element selection and page-error capture are available. Click for help on this tab.'
    }
    if (companionStatus.value === 'waiting') return 'Checking for the TwiCC companion script…'
    return 'No companion script on this page — real history, element selection and error capture are off. Click to see how to add it.'
})

// Open the Browser tab's help page (reference open — no dismiss switch).
// While no companion is detected, also re-surface the snippet banner the
// help points at (it may have been dismissed).
function onCompanionIconClick() {
    if (companionStatus.value === 'absent') snippetDismissed.value = false
    showHelp('browser-tab', { showDontShowAgain: false })
}

// Open the Browser tab's help page from the blank-state callout (reference
// open — no dismiss switch, same as the companion icon).
function openBrowserHelp() {
    showHelp('browser-tab', { showDontShowAgain: false })
}

// ── Select-area mode: toggled from the companion menu; the actual picking
// (interaction-blocking overlay + outline on the hovered/tapped element)
// happens inside the embedded page, driven by the companion. The flag only
// mirrors what we asked for — it resets on hello/bye above, since the overlay
// lives and dies with the page's document. `selectState` is the companion's
// report on the highlighted element (null until one is highlighted).
const selectAreaActive = ref(false)
const selectState = ref(null)

// A single in-flight screenshot capture: { resolve, reject, timer }, or null.
// The companion answers with a select-capture message (resolved in
// onWindowMessage). Plain closure var — only the promise consumer cares, not
// the template.
let pendingCapture = null
function rejectPendingCapture(reason) {
    if (!pendingCapture) return
    const { reject, timer } = pendingCapture
    pendingCapture = null
    clearTimeout(timer)
    reject(new Error(reason))
}

function setSelectArea(enabled) {
    if (selectAreaActive.value === enabled) return
    selectAreaActive.value = enabled
    selectState.value = null
    rejectPendingCapture('select mode changed')
    // Drop any open comment widget so a stale one can't flash back on re-entry.
    commentPosition.value = null
    sendToCompanion(hostMessage('command', { action: 'select-mode', enabled }))
}

function selectNav(direction) {
    sendToCompanion(hostMessage('command', { action: 'select-nav', direction }))
}

function selectClear() {
    sendToCompanion(hostMessage('command', { action: 'select-clear' }))
}

function selectComment() {
    sendToCompanion(hostMessage('command', { action: 'select-describe' }))
}

// Passed to the comment widget's "Include screenshot" switch. Fires a capture
// command and resolves with the PNG data URL when the companion answers (see
// the select-capture handler); rejects on timeout / disconnection so the
// switch can revert.
const CAPTURE_TIMEOUT_MS = 15000
function captureScreenshot() {
    if (companionStatus.value !== 'present') {
        return Promise.reject(new Error('companion not connected'))
    }
    if (pendingCapture) return Promise.reject(new Error('a capture is already in progress'))
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => rejectPendingCapture('capture timed out'), CAPTURE_TIMEOUT_MS)
        pendingCapture = { resolve, reject, timer }
        sendToCompanion(hostMessage('command', { action: 'select-capture' }))
    })
}

// dataUrl → File → draft attachment (the same path a manual image upload
// takes, so provider validation / resize / caps all apply).
async function attachScreenshot(dataUrl) {
    const blob = await (await fetch(dataUrl)).blob()
    const file = new File([blob], `browser-capture-${Date.now()}.png`, { type: 'image/png' })
    await store.addAttachment(props.sessionId, file)
}

// ── Comment widget: reuses the text-selection comment window for two sources —
// a picked element (select-area, with the screenshot switch) and the page's
// captured errors. One shared position/text/subject drives a single instance;
// `commentAllowScreenshot` gates the Include-screenshot switch (element only).
// Initial position: horizontally centered, top edge right below the anchor —
// the user can drag it anywhere afterwards, as usual.
const selectToolbarRef = ref(null)
const commentText = ref('')
const commentPosition = ref(null)          // viewport anchor, or null = closed
const commentSubject = ref('selected area')
const commentAllowScreenshot = ref(false)
// Rendered into the formatted message header so the agent knows which page the
// element / errors live on.
const commentSourceLabel = computed(() => `from the browser page at ${currentUrl.value}`)

function openSelectComment(description) {
    const rect = selectToolbarRef.value?.$el?.getBoundingClientRect()
    if (!rect) return
    commentText.value = description
    commentSubject.value = 'selected area'
    commentAllowScreenshot.value = true
    commentPosition.value = { top: rect.bottom, left: rect.left + rect.width / 2, above: false }
}

// ── Page errors: the companion buffers console.error / uncaught exceptions /
// unhandled rejections and pushes the list here; a toolbar badge shows the
// count and clicking hands them to the agent via the same comment widget.
const pageErrors = ref([])       // [{ kind, text }], capped by the companion
const pageErrorTotal = ref(0)    // true count (may exceed the capped list)

function formatErrorList() {
    const body = pageErrors.value.map((e) => `[${e.kind}] ${e.text}`).join('\n\n')
    if (pageErrorTotal.value > pageErrors.value.length) {
        return `(showing the last ${pageErrors.value.length} of ${pageErrorTotal.value} errors)\n\n${body}`
    }
    return body
}

function openErrorComment(event) {
    if (!pageErrors.value.length) return
    const rect = event.currentTarget?.getBoundingClientRect()
    if (!rect) return
    commentText.value = formatErrorList()
    commentSubject.value = 'page errors'
    commentAllowScreenshot.value = false
    commentPosition.value = { top: rect.bottom, left: rect.left + rect.width / 2, above: false }
}

// ── Responsive mode: the stage (the iframe's placeholder box) takes an exact
// CSS-pixel viewport size instead of filling the pane. Pure host-side feature
// — no companion involved: the page reacts to its iframe size exactly as to a
// small window. All the machinery (toolbar, hatched canvas, drag handles)
// lives in the shared frames/ViewportStage + ViewportToolbar; only the mode
// flag and the dimensions are the pane's.
const responsiveActive = ref(false)
const viewportWidth = ref(375)
const viewportHeight = ref(667)
const viewportStageRef = ref(null)

// ── Save-URL dialog -----------------------------------------------------------
const project = computed(() => store.getProject(props.projectId))
const mainRepoProject = computed(() =>
    project.value?.worktree_of ? store.getProject(project.value.worktree_of) : null
)
const memberWorkspaces = computed(() =>
    workspacesStore.workspaces.filter(
        (w) => !w.archived && workspacesStore.workspaceContainsProject(w.id, props.projectId)
    )
)
const canSave = computed(() => !!currentUrl.value && !!project.value)
const saveError = ref('')

// The levels that can hold saved URLs, each with its current entry list —
// the worktree/project itself, its main repo (worktree case), and each
// member workspace. Feeds both the save dialog's target picker and the
// Home dropdown below.
const saveLevels = computed(() => {
    const levels = []
    if (project.value) {
        levels.push({ key: 'project', entries: project.value.browser_urls || [] })
    }
    if (mainRepoProject.value) {
        levels.push({ key: 'main-repo', entries: mainRepoProject.value.browser_urls || [] })
    }
    for (const ws of memberWorkspaces.value) {
        levels.push({ key: `ws:${ws.id}`, entries: ws.browserUrls || [], ws })
    }
    return levels
})

async function putProjectBrowserUrls(projectId, entries) {
    const response = await apiFetch(`/api/projects/${projectId}/`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ browser_urls: entries }),
    })
    if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw new Error(data.error || `Failed to save (${response.status})`)
    }
    store.updateProject(await response.json())
}

// Persist a level's new entry list: PUT for the project levels, whole-blob
// store update for a workspace.
async function applyLevelEntries(levelKey, entries) {
    if (levelKey === 'project') {
        await putProjectBrowserUrls(props.projectId, entries)
    } else if (levelKey === 'main-repo') {
        await putProjectBrowserUrls(project.value.worktree_of, entries)
    } else if (levelKey.startsWith('ws:')) {
        workspacesStore.updateWorkspace(levelKey.slice(3), { browserUrls: entries })
    }
}

// True when the URL currently shown is already saved at ANY level (worktree
// project, main repo, or a member workspace) — tints the bookmark button.
const isCurrentUrlSaved = computed(() =>
    !!currentUrl.value &&
    saveLevels.value.some((level) => level.entries.some((e) => e.url === currentUrl.value))
)

const saveDialogRef = ref(null)
const saveButtonRef = ref(null)
const saveLabelInputRef = ref(null)
const saveFormId = `browser-save-form-${instanceId}`
const saveLabel = ref('')
const saveTarget = ref('project')
const saveMakeDefault = ref(false)

function openSaveDialog() {
    if (!canSave.value) return
    saveError.value = ''
    saveLabel.value = ''
    saveTarget.value = 'project'
    saveMakeDefault.value = false
    if (saveDialogRef.value) saveDialogRef.value.open = true
}

// wa-button doesn't expose `form` as a property — set the attribute so the
// footer submit button drives the form inside the dialog body.
function onSaveDialogShow(event) {
    if (event.target !== saveDialogRef.value) return
    nextTick(() => saveButtonRef.value?.setAttribute('form', saveFormId))
}

function onSaveDialogAfterShow(event) {
    if (event.target !== saveDialogRef.value) return
    saveLabelInputRef.value?.focus()
}

function closeSaveDialog() {
    if (saveDialogRef.value) saveDialogRef.value.open = false
}

async function submitSave() {
    const level = saveLevels.value.find((l) => l.key === saveTarget.value)
    if (!level || !currentUrl.value) return
    saveError.value = ''
    try {
        await applyLevelEntries(
            level.key,
            addBrowserUrlEntry(level.entries, currentUrl.value, {
                label: saveLabel.value,
                setDefault: saveMakeDefault.value,
            })
        )
        if (saveDialogRef.value) saveDialogRef.value.open = false
    } catch (e) {
        saveError.value = e.message || 'Failed to save URL'
    }
}

// ── Home options: every saved URL across the levels above, deduped by URL
// (levels often share one), so the count reflects DISTINCT destinations:
// one → the Home button just navigates to it; several → a dropdown lets the
// user pick. Priority order (project → main repo → workspace) keeps the badge
// of the first level that saves a given URL. Each option carries its level so
// the per-entry actions (remove, set as Home default) patch the right list.
const homeOptions = computed(() => {
    const opts = []
    const seen = new Set()
    for (const level of saveLevels.value) {
        const def = defaultBrowserUrlEntry(level.entries)
        for (const entry of level.entries) {
            if (!entry?.url || seen.has(entry.url)) continue
            seen.add(entry.url)
            opts.push({
                key: `${level.key}|${entry.url}`,
                levelKey: level.key,
                url: entry.url,
                label: entry.label || null,
                ws: level.ws || null,
                isLevelDefault: def?.url === entry.url,
            })
        }
    }
    return opts
})

// The URL the plain Home button navigates to: the resolved default (project
// chain — flagged entry first — then workspaces), or the first saved option.
const primaryHomeUrl = computed(() => defaultUrl.value || homeOptions.value[0]?.url)

// ── Command palette: the two toolbar actions worth reaching by keyboard.
// Registered only while this pane is the shown tab, like FilePane's edit
// toggle — both act on "the browser you are looking at". The watch never fires
// on unmount, so onBeforeUnmount unregisters too.
watch(
    () => props.active,
    (active) => {
        if (active) {
            registerCommand({
                id: openSavedUrlCommandId,
                label: 'Open a Saved URL…',
                icon: 'house',
                category: 'navigation',
                when: () => homeOptions.value.length > 0,
                items: () => homeOptions.value.map(opt => ({
                    id: opt.key,
                    label: opt.label ? `${opt.label} — ${opt.url}` : opt.url,
                    active: opt.url === currentUrl.value,
                    action: () => navigate(opt.url),
                })),
            })
            registerCommand({
                id: saveUrlCommandId,
                label: 'Save Current URL…',
                icon: 'bookmark',
                category: 'ui',
                // Nothing loaded yet → nothing to save (same gate as the button).
                when: () => canSave.value,
                action: () => openSaveDialog(),
            })
        } else {
            unregisterCommand(openSavedUrlCommandId)
            unregisterCommand(saveUrlCommandId)
        }
    },
    { immediate: true },
)

// Set by the per-entry action buttons so the click that triggered them never
// doubles as a wa-select navigation (belt and suspenders next to @click.stop).
let suppressNextHomeSelect = false

function onHomeSelect(event) {
    if (suppressNextHomeSelect) {
        suppressNextHomeSelect = false
        return
    }
    const url = event.detail?.item?.value
    if (url) navigate(url)
}

async function removeSavedUrl(opt) {
    suppressNextHomeSelect = true
    const level = saveLevels.value.find((l) => l.key === opt.levelKey)
    if (!level) return
    saveError.value = ''
    try {
        await applyLevelEntries(level.key, removeBrowserUrlEntry(level.entries, opt.url))
    } catch (e) {
        saveError.value = e.message || 'Failed to remove URL'
    }
}

async function makeDefaultSavedUrl(opt) {
    suppressNextHomeSelect = true
    const level = saveLevels.value.find((l) => l.key === opt.levelKey)
    if (!level) return
    saveError.value = ''
    try {
        await applyLevelEntries(level.key, setDefaultBrowserUrlEntry(level.entries, opt.url))
    } catch (e) {
        saveError.value = e.message || 'Failed to set the default URL'
    }
}
</script>

<template>
    <div class="browser-pane" :class="{ 'browser-pane--fullscreen': isFullscreen }">
        <div class="browser-toolbar">
            <div class="browser-toolbar-left">
                <wa-button :id="`browser-back-${instanceId}`" appearance="plain" size="small" class="browser-btn reduced-height" :disabled="!canGoBack" @click="goBack">
                    <wa-icon name="arrow-left"></wa-icon>
                </wa-button>
                <AppTooltip :for="`browser-back-${instanceId}`">Back</AppTooltip>
                <wa-button :id="`browser-forward-${instanceId}`" appearance="plain" size="small" class="browser-btn reduced-height" :disabled="!canGoForward" @click="goForward">
                    <wa-icon name="arrow-right"></wa-icon>
                </wa-button>
                <AppTooltip :for="`browser-forward-${instanceId}`">Forward</AppTooltip>
                <wa-button :id="`browser-refresh-${instanceId}`" appearance="plain" size="small" class="browser-btn reduced-height" :disabled="!currentUrl" @click="refresh">
                    <wa-icon name="rotate-right"></wa-icon>
                </wa-button>
                <AppTooltip :for="`browser-refresh-${instanceId}`">Refresh</AppTooltip>
                <!-- Home: a plain button for a single saved URL, or a dropdown
                     (like Save) when several levels — worktree, main repo,
                     workspace — each define one. WA custom events are stopped
                     from bubbling as in the Save menu. -->
                <wa-dropdown
                    v-if="homeOptions.length > 1"
                    placement="bottom-start"
                    @click.stop
                    @wa-select.stop="onHomeSelect"
                    @wa-show.stop
                    @wa-hide.stop
                    @wa-after-show.stop
                    @wa-after-hide.stop
                >
                    <wa-button :id="`browser-home-${instanceId}`" slot="trigger" appearance="plain" size="small" class="browser-btn reduced-height">
                        <wa-icon name="house"></wa-icon>
                    </wa-button>
                    <wa-dropdown-item disabled class="save-menu-header">Open a saved URL…</wa-dropdown-item>
                    <wa-dropdown-item v-for="opt in homeOptions" :key="opt.key" :value="opt.url">
                        <div class="home-menu-item">
                            <WorktreeBadge v-if="opt.levelKey === 'project' && mainRepoProject" :project-id="props.projectId" />
                            <ProjectBadge v-else-if="opt.levelKey === 'project'" :project-id="props.projectId" />
                            <ProjectBadge v-else-if="opt.levelKey === 'main-repo'" :project-id="mainRepoProject.id" />
                            <span v-else class="save-menu-ws">
                                <wa-icon name="layer-group" :style="opt.ws.color ? { color: opt.ws.color } : null"></wa-icon>
                                {{ opt.ws.name }}
                            </span>
                            <span class="home-menu-text">
                                <span v-if="opt.label" class="home-menu-label">{{ opt.label }}</span>
                                <span class="home-menu-url">{{ opt.url }}</span>
                            </span>
                            <span class="home-menu-actions">
                                <wa-icon
                                    v-if="opt.isLevelDefault"
                                    name="star"
                                    class="home-menu-default-star"
                                    title="Home default for this level"
                                ></wa-icon>
                                <button
                                    v-else
                                    type="button"
                                    class="home-menu-action"
                                    title="Make it the Home default"
                                    @click.stop="makeDefaultSavedUrl(opt)"
                                >
                                    <wa-icon name="star"></wa-icon>
                                </button>
                                <button
                                    type="button"
                                    class="home-menu-action home-menu-action--danger"
                                    title="Remove this saved URL"
                                    @click.stop="removeSavedUrl(opt)"
                                >
                                    <wa-icon name="trash"></wa-icon>
                                </button>
                            </span>
                        </div>
                    </wa-dropdown-item>
                </wa-dropdown>
                <template v-else>
                    <wa-button :id="`browser-home-${instanceId}`" appearance="plain" size="small" class="browser-btn reduced-height" :disabled="!primaryHomeUrl" @click="goHome">
                        <wa-icon name="house"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="`browser-home-${instanceId}`">{{ primaryHomeUrl ? `Home — ${primaryHomeUrl}` : 'Home (no saved URL for this project)' }}</AppTooltip>
                </template>
                <AppTooltip v-if="homeOptions.length > 1" :for="`browser-home-${instanceId}`">Home — choose a saved URL</AppTooltip>
            </div>

            <wa-input
                ref="addressInputRef"
                class="browser-address reduced-height"
                size="small"
                autocomplete="off"
                placeholder="Enter a URL — e.g. localhost:5173"
                :value="inputUrl"
                @input="inputUrl = $event.target.value"
                @focus="addressFocused = true"
                @blur="addressFocused = false"
                @keydown.enter.prevent="onAddressSubmit"
            >
                <wa-spinner v-if="loading" slot="start"></wa-spinner>
                <wa-icon v-else slot="start" name="globe"></wa-icon>
            </wa-input>

            <!-- All right-side controls act on a loaded page (save it, open it,
                 expand it, companion, responsive) — hidden on the blank state
                 where there is no page yet; only the address bar and its nav
                 buttons remain. -->
            <div v-if="currentUrl" class="browser-toolbar-right">
                <!-- Save current URL into a level's saved-URLs list — opens a
                     mini dialog (target level, optional label, default flag). -->
                <wa-button
                    :id="`browser-save-${instanceId}`"
                    appearance="plain"
                    size="small"
                    class="browser-btn reduced-height save-toggle"
                    :class="{ saved: isCurrentUrlSaved }"
                    :disabled="!canSave"
                    @click="openSaveDialog"
                >
                    <wa-icon name="bookmark"></wa-icon>
                </wa-button>
                <AppTooltip :for="`browser-save-${instanceId}`">
                    {{ isCurrentUrlSaved ? 'URL saved — edit…' : 'Save this URL…' }}
                </AppTooltip>

                <wa-button :id="`browser-external-${instanceId}`" appearance="plain" size="small" class="browser-btn reduced-height" :disabled="!currentUrl" @click="openExternal">
                    <wa-icon name="arrow-up-right-from-square"></wa-icon>
                </wa-button>
                <AppTooltip :for="`browser-external-${instanceId}`">Open in a new browser tab</AppTooltip>

                <wa-button
                    :id="fullscreenButtonId"
                    appearance="plain"
                    size="small"
                    class="browser-btn reduced-height"
                    @click="toggleFullscreen"
                >
                    <wa-icon :name="isFullscreen ? 'compress' : 'expand'"></wa-icon>
                </wa-button>
                <AppTooltip :for="fullscreenButtonId">
                    {{ isFullscreen ? 'Exit full screen' : 'Full screen' }}
                </AppTooltip>

                <!-- Companion status button (plain, always). The plug's colour
                     reflects the connection state; a tap reveals its status/help
                     tooltip, and while disconnected a click re-opens the
                     how-to-add hint. force + click trigger so the tooltip shows
                     on touch too (no hover there). -->
                <wa-button
                    :id="`browser-companion-${instanceId}`"
                    appearance="plain"
                    size="small"
                    class="browser-btn reduced-height companion-status"
                    :class="companionStatus"
                    @click="onCompanionIconClick"
                >
                    <wa-icon name="plug"></wa-icon>
                </wa-button>
                <AppTooltip
                    :for="`browser-companion-${instanceId}`"
                    force
                    trigger="hover focus click"
                >{{ companionTooltip }}</AppTooltip>

                <!-- Responsive-mode toggle: gives the embedded page an exact
                     device-size viewport (toolbar below + resizable stage).
                     Host-side only — no companion needed. -->
                <wa-button
                    :id="`browser-responsive-${instanceId}`"
                    appearance="plain"
                    size="small"
                    class="browser-btn reduced-height responsive-toggle"
                    :class="{ active: responsiveActive }"
                    :disabled="!currentUrl"
                    @click="responsiveActive = !responsiveActive"
                >
                    <wa-icon name="mobile-screen-button"></wa-icon>
                </wa-button>
                <AppTooltip :for="`browser-responsive-${instanceId}`">
                    {{ responsiveActive ? 'Exit responsive mode' : 'Responsive mode — preview at a device size' }}
                </AppTooltip>

                <!-- Select-area toggle (needs the companion, so only shown once
                     connected). Turns green while the picking mode is active. -->
                <wa-button
                    v-if="companionStatus === 'present'"
                    :id="`browser-select-toggle-${instanceId}`"
                    appearance="plain"
                    size="small"
                    class="browser-btn reduced-height select-toggle"
                    :class="{ active: selectAreaActive }"
                    @click="setSelectArea(!selectAreaActive)"
                >
                    <wa-icon name="arrow-pointer"></wa-icon>
                </wa-button>
                <AppTooltip
                    v-if="companionStatus === 'present'"
                    :for="`browser-select-toggle-${instanceId}`"
                >{{ selectAreaActive ? 'Stop selecting an element' : 'Select an element' }}</AppTooltip>

                <!-- Page-errors indicator: shown only when the companion has
                     reported errors. The count sits next to a warning glyph;
                     clicking hands the list to the agent via the comment widget. -->
                <wa-button
                    v-if="companionStatus === 'present' && pageErrors.length"
                    :id="`browser-errors-${instanceId}`"
                    appearance="plain"
                    size="small"
                    class="browser-btn reduced-height error-indicator"
                    @click="openErrorComment"
                >
                    <wa-icon name="triangle-exclamation"></wa-icon>
                    <span class="error-count">{{ pageErrorTotal }}</span>
                </wa-button>
                <AppTooltip
                    v-if="companionStatus === 'present' && pageErrors.length"
                    :for="`browser-errors-${instanceId}`"
                >{{ pageErrorTotal }} error{{ pageErrorTotal > 1 ? 's' : '' }} on this page — click to add them to your message</AppTooltip>
            </div>
        </div>

        <!-- Responsive-viewport toolbar: shown while the responsive mode is on
             (always ABOVE the select toolbar when both are open). -->
        <ViewportToolbar
            v-if="responsiveActive"
            v-model:width="viewportWidth"
            v-model:height="viewportHeight"
            @close="responsiveActive = false"
        />

        <!-- Select-area toolbar: shown while the picking mode is on. The
             navigation buttons walk the page's DOM from the highlighted
             element — every step is executed by the companion in the page. -->
        <SelectAreaToolbar
            v-if="selectAreaActive"
            ref="selectToolbarRef"
            :state="selectState"
            @nav="selectNav"
            @clear="selectClear"
            @comment="selectComment"
            @close="setSelectArea(false)"
        />

        <!-- Comment widget (teleported to body like every other consumer —
             avoids overflow clipping and sits above fullscreen). Shared by the
             select-area flow (element description, with the screenshot switch)
             and the page-errors flow (error list, no screenshot). No source
             selection to clear: the "quote" is the text we hand it. -->
        <Teleport to="body">
            <TextSelectionComment
                v-if="commentPosition"
                :selected-text="commentText"
                :position="commentPosition"
                :source-label="commentSourceLabel"
                :subject="commentSubject"
                auto-expand
                :clear-source-selection="() => {}"
                :capture-screenshot="commentAllowScreenshot ? captureScreenshot : null"
                :attach-screenshot="commentAllowScreenshot ? attachScreenshot : null"
                @close="commentPosition = null"
            />
        </Teleport>

        <wa-callout v-if="saveError" variant="danger" size="small" class="browser-banner">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            {{ saveError }}
        </wa-callout>

        <wa-callout v-if="probeResult" variant="warning" size="small" class="browser-banner">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            <template v-if="probeResult.reachable === false">
                The server did not respond ({{ probeResult.reason }}) — is it running?
            </template>
            <template v-else>
                This site refuses to be embedded ({{ probeResult.reason }}) — the frame
                below will likely stay blank. Use "Open in a new browser tab" instead, or
                see the <a href="#" class="browser-empty-help-link" @click.prevent="openBrowserHelp">Browser tab guide</a>
                to allow framing in dev.
            </template>
        </wa-callout>

        <wa-callout v-if="mixedContentBlocked" variant="warning" size="small" class="browser-banner">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            TwiCC is served over https, so the browser blocks embedding this http://
            URL (mixed content). Open it in a new tab instead, or serve it over https —
            see the <a href="#" class="browser-empty-help-link" @click.prevent="openBrowserHelp">Browser tab guide</a>.
        </wa-callout>

        <wa-callout v-if="showCompanionHint" variant="neutral" size="small" class="browser-banner">
            <wa-icon slot="icon" name="plug"></wa-icon>
            <div class="companion-hint">
                <span>
                    This page doesn't include the TwiCC companion script. Add it
                    to your dev page to unlock real navigation history, element
                    selection (click a part of the page to describe it to the
                    agent), and page-error capture (see the
                    <a href="#" class="browser-empty-help-link" @click.prevent="openBrowserHelp">Browser tab guide</a>):
                </span>
                <code class="companion-snippet">{{ companionSnippet }}</code>
                <div class="companion-hint-actions">
                    <wa-button size="small" appearance="outlined" @click="copySnippet">
                        {{ snippetCopied ? 'Copied!' : 'Copy snippet' }}
                    </wa-button>
                    <wa-button size="small" appearance="plain" @click="snippetDismissed = true">Dismiss</wa-button>
                </div>
            </div>
        </wa-callout>

        <!-- The stage wrappers render in BOTH modes (only restyled) so the
             PersistentFrame component never unmounts on a mode toggle — a
             remount would re-register the pooled iframe and reload it. -->
        <ViewportStage
            ref="viewportStageRef"
            :active="responsiveActive"
            v-model:width="viewportWidth"
            v-model:height="viewportHeight"
            :show-stage="!!(everActivated && currentUrl && !mixedContentBlocked)"
        >
            <PersistentFrame
                ref="persistentFrameRef"
                :frame-id="`browser:${instanceId}`"
                :src="frameSrc"
                :remount-key="frameKey"
                :attrs="BROWSER_FRAME_ATTRS"
                :elevated="props.frameElevated"
                :fullscreen="isFullscreen"
                :clip-el="viewportStageRef?.bodyEl ?? null"
                class="browser-frame"
                @load="onFrameLoad"
            />
            <template #empty>
                <div v-if="!currentUrl" class="browser-empty">
                    <wa-icon name="globe" class="browser-empty-icon"></wa-icon>
                    <p>Enter a URL above to preview your project — e.g. your dev server.</p>
                    <p class="browser-empty-hint">
                        Once a page is loaded, the toolbar lets you save its URL
                        for this project or one of its workspaces.
                    </p>
                    <wa-callout variant="neutral" size="small" class="browser-empty-callout">
                        <wa-icon slot="icon" name="circle-info"></wa-icon>
                        Getting your dev server to display here often needs a few
                        dev-only settings (allow framing, cookies, the companion
                        script). It's worth reading the
                        <a href="#" class="browser-empty-help-link" @click.prevent="openBrowserHelp">Browser tab guide</a>
                        first.
                    </wa-callout>
                </div>
            </template>
        </ViewportStage>

        <!-- Save-URL mini dialog: pick the level, optionally name the entry,
             optionally flag it as the level's Home default. Show/after-show
             handlers are target-guarded (nested wa-* children bubble the same
             custom event names). -->
        <wa-dialog
            ref="saveDialogRef"
            label="Save this URL"
            class="browser-save-dialog"
            @wa-show="onSaveDialogShow"
            @wa-after-show="onSaveDialogAfterShow"
        >
            <form :id="saveFormId" class="save-dialog-form" @submit.prevent="submitSave">
                <div class="save-dialog-url">{{ currentUrl }}</div>
                <wa-input
                    ref="saveLabelInputRef"
                    label="Label (optional)"
                    size="small"
                    autocomplete="off"
                    maxlength="100"
                    placeholder="e.g. Storybook, Front dev…"
                    :value="saveLabel"
                    @input="saveLabel = $event.target.value"
                ></wa-input>
                <div class="save-dialog-levels">
                    <span class="save-dialog-levels-title">Save for…</span>
                    <label v-for="level in saveLevels" :key="level.key" class="save-dialog-level">
                        <input
                            type="radio"
                            name="save-target"
                            :value="level.key"
                            :checked="saveTarget === level.key"
                            @change="saveTarget = level.key"
                        />
                        <WorktreeBadge v-if="level.key === 'project' && mainRepoProject" :project-id="props.projectId" />
                        <ProjectBadge v-else-if="level.key === 'project'" :project-id="props.projectId" />
                        <ProjectBadge v-else-if="level.key === 'main-repo'" :project-id="mainRepoProject.id" />
                        <span v-else class="save-menu-ws">
                            <wa-icon name="layer-group" :style="level.ws.color ? { color: level.ws.color } : null"></wa-icon>
                            {{ level.ws.name }}
                        </span>
                        <span v-if="level.entries.some((e) => e.url === currentUrl)" class="save-menu-saved">saved</span>
                    </label>
                </div>
                <wa-checkbox
                    size="small"
                    :checked="saveMakeDefault"
                    @change="saveMakeDefault = $event.target.checked"
                >Make it the Home default</wa-checkbox>
                <wa-callout v-if="saveError" variant="danger" size="small">{{ saveError }}</wa-callout>
            </form>
            <div slot="footer" class="save-dialog-footer">
                <wa-button variant="neutral" appearance="outlined" size="small" @click="closeSaveDialog">Cancel</wa-button>
                <wa-button ref="saveButtonRef" variant="brand" size="small" type="submit">Save</wa-button>
            </div>
        </wa-dialog>
    </div>
</template>

<style scoped>
.browser-pane {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
}

/* Full-window: the whole pane expands IN PLACE (position:fixed) rather than
   teleporting — moving the iframe would reload it. The toolbar stays on top; the
   pooled frame follows its placeholder in the body and switches to the
   fullscreen z-tier (layered above this fixed pane, geometrically only over the
   body). ProjectView drops .main-content's containment and lifts it above the
   sidebar/divider while expanded (via the injected setter), so this fixed
   overlay covers the whole window. */
.browser-pane--fullscreen {
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: var(--wa-color-surface-default);
}

.browser-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    padding: var(--wa-space-2xs);
    border-bottom: 1px solid var(--wa-color-border-quiet);
    flex-shrink: 0;
}

/* Nav buttons before the address, controls after it — grouped so the toolbar
   has three flex children (left · address · right) that wrap as units. */
.browser-toolbar-left,
.browser-toolbar-right {
    display: flex;
    align-items: center;
    flex-shrink: 0;
}

/* Keep the controls flush right, even once the address has grown or the row
   has wrapped them onto their own line. */
.browser-toolbar-right {
    margin-left: auto;
}

.browser-btn {
    flex-shrink: 0;
}

.browser-address {
    flex: 1;
    min-width: min(10rem, 90%);
}

/* reduced-height shrinks the whole control through its font-size; the typed
   URL (native input part) and the start-slot glyph (globe / loading spinner)
   get the small size back. */
.browser-address::part(input) {
    font-size: var(--wa-font-size-s);
}

.browser-address > [slot='start'] {
    font-size: var(--wa-font-size-s);
}

/* Companion status is a normal toolbar button now; the plug's colour + dim
   reflect the connection state (icon targeted directly — currentColor glyph).
   The base (dim/quiet) is the transient 'waiting' state; 'absent' turns it a
   full-opacity alert red (companion not installed) and 'present' the brand
   colour (connected). */
.companion-status {
    opacity: 0.55;
}

.companion-status wa-icon {
    color: var(--wa-color-text-quiet);
}

.companion-status.absent {
    opacity: 1;
}

.companion-status.absent wa-icon {
    color: var(--wa-color-danger-60);
}

.companion-status.present {
    opacity: 1;
}

.companion-status.present wa-icon {
    color: var(--wa-color-brand-fill-loud);
}

/* Select-area / responsive toggles: brand colour while the mode is active,
   normal otherwise (mirrors the connected-companion plug colour). */
.select-toggle.active wa-icon,
.responsive-toggle.active wa-icon,
.save-toggle.saved wa-icon {
    color: var(--wa-color-brand-fill-loud);
}

/* Page-errors indicator: warning glyph + count, in the danger colour. */
.error-indicator wa-icon,
.error-indicator .error-count {
    color: var(--wa-color-danger-fill-loud);
}

.error-indicator .error-count {
    margin-inline-start: var(--wa-space-3xs);
    font-size: var(--wa-font-size-s);
    font-variant-numeric: tabular-nums;
}

.browser-banner {
    margin: var(--wa-space-xs);
    flex-shrink: 0;
}

.companion-hint {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.companion-snippet {
    font-size: var(--wa-font-size-xs);
    background: var(--wa-color-surface-lowered);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    border-radius: var(--wa-border-radius-s);
    user-select: all;
    overflow-wrap: anywhere;
}

.companion-hint-actions {
    display: flex;
    gap: var(--wa-space-xs);
}

.save-menu-header::part(label) {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.save-menu-saved {
    margin-left: var(--wa-space-xs);
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-text-quiet);
}

/* Home menu: each level's badge stacked over its saved URL (label + URL),
   with the per-entry actions (default star, remove) pinned to the right. */
.home-menu-item {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    column-gap: var(--wa-space-s);
    row-gap: var(--wa-space-3xs);
    min-width: 0;
}

.home-menu-item > :first-child {
    grid-column: 1;
}

.home-menu-text {
    grid-column: 1;
    display: flex;
    flex-direction: column;
    gap: 0;
    min-width: 0;
}

.home-menu-label {
    font-size: var(--wa-font-size-xs);
}

.home-menu-url {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    overflow-wrap: anywhere;
}

.home-menu-actions {
    grid-column: 2;
    grid-row: 1 / span 2;
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

/* Native buttons: WA's native.css gives them form-control height — reset. */
.home-menu-action {
    display: inline-flex;
    align-items: center;
    height: auto;
    padding: var(--wa-space-3xs);
    border: none;
    background: none;
    cursor: pointer;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

.home-menu-action:hover {
    color: var(--wa-color-text-normal);
}

.home-menu-action--danger:hover {
    color: var(--wa-color-danger-fill-loud);
}

.home-menu-default-star {
    padding: var(--wa-space-3xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-warning-fill-loud);
}

/* Workspace level marker: layer-group badge + name inline, so it lines up with
   the color dot the project/worktree badges render at the same start position. */
.save-menu-ws {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
}

/* Placeholder sizing only — the pooled iframe (in FrameHost) carries the
   border/background; PersistentFrame positions it over this box. */
.browser-frame {
    flex: 1;
    width: 100%;
    height: 100%;
}

/* `safe center` + overflow, not a plain center: the placeholder's content is
   ~250px of incompressible text, and the body shrinks to nothing when the pane
   is short (mobile with the on-screen keyboard open). A plain `center` spills
   the excess symmetrically — including UPWARD, over the toolbar, since the
   placeholder paints after it — and the pane's own overflow clip can't catch
   that. `safe` falls back to start-alignment once it no longer fits, so the
   overflow goes one way and stays reachable by scroll. */
.browser-empty {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: safe center;
    overflow-y: auto;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    text-align: center;
    padding: var(--wa-space-l);
}

.browser-empty-icon {
    font-size: 2.5rem;
    opacity: 0.5;
}

.browser-empty p {
    margin: 0;
}

.browser-empty-hint {
    font-size: var(--wa-font-size-s);
}

/* Dev-server setup reminder: constrained width + left-aligned text (the
   surrounding placeholder centers everything), pointing at the Browser tab
   help page. */
.browser-empty-callout {
    max-width: 32rem;
    text-align: start;
    font-size: var(--wa-font-size-s);
}

.browser-empty-help-link {
    color: var(--wa-color-brand-fill-loud);
    text-decoration: underline;
    cursor: pointer;
}
/* ── Save-URL mini dialog ─────────────────────────────────────────────── */
.browser-save-dialog {
    --width: min(420px, calc(100vw - 2rem));
}

.save-dialog-form {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.save-dialog-url {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    overflow-wrap: anywhere;
}

.save-dialog-levels {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.save-dialog-levels-title {
    font-size: var(--wa-font-size-s);
}

.save-dialog-level {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    cursor: pointer;
}

.save-dialog-level input[type='radio'] {
    margin: 0;
    accent-color: var(--wa-color-brand-fill-loud);
}

.save-dialog-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-xs);
}
</style>
