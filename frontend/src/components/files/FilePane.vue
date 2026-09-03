<script setup>
import { ref, watch, computed, nextTick, useId, inject, onMounted, onBeforeUnmount } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import { apiFetch } from '../../utils/api'
import { base64UrlEncode, encodeFilePathSegments } from '../../utils/download'
import { useArtifactBroker } from '../../composables/useArtifactBroker'
import { useSettingsStore } from '../../stores/settings'
import { useCommandRegistry } from '../../composables/useCommandRegistry'
import { usePanZoom } from '../../composables/usePanZoom'
import { useFocusRetry } from '../../composables/useFocusRetry'
import MarkdownContent from '../ui/MarkdownContent.vue'
import MermaidDiagram from '../ui/MermaidDiagram.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import CodeEditor from '../editor/CodeEditor.vue'
import DiffEditor from '../editor/DiffEditor.vue'
import TextSelectionComment from '../session/detail/TextSelectionComment.vue'
import ArtifactBookmarkButton from '../artifacts/ArtifactBookmarkButton.vue'
import ArtifactBrokerPrompt from '../artifacts/ArtifactBrokerPrompt.vue'
import PersistentFrame from '../frames/PersistentFrame.vue'
import SelectAreaToolbar from '../frames/SelectAreaToolbar.vue'
import ViewportStage from '../frames/ViewportStage.vue'
import ViewportToolbar from '../frames/ViewportToolbar.vue'
import { useDataStore } from '../../stores/data'
import { useFramePoolStore } from '../../stores/framePool'
import { useTextSelectionComment } from '../../composables/useTextSelectionComment'

// Stable identity — an inline literal would churn the frame descriptor watch.
const HTML_PREVIEW_FRAME_ATTRS = { sandbox: 'allow-scripts allow-same-origin allow-forms', title: 'HTML preview' }

const props = defineProps({
    projectId: String,
    sessionId: String,
    filePath: String,  // absolute path (also used in diff mode for language detection and save)
    isDraft: {
        type: Boolean,
        default: false,
    },
    /** Git commit SHA (null = index/uncommitted, undefined = not a git context) */
    commitSha: { type: String, default: undefined },
    // --- Diff mode props ---
    diffMode: {
        type: Boolean,
        default: false,
    },
    originalContent: {
        type: String,
        default: null,  // left side (null = new file, everything shows as added)
    },
    modifiedContent: {
        type: String,
        default: null,  // right side (null = deleted file, everything shows as removed)
    },
    diffReadOnly: {
        type: Boolean,
        default: true,  // true for commit diffs, false for index (editable)
    },
    apiPrefix: {
        type: String,
        default: null,
    },
    rootRestriction: {
        type: String,
        default: null,
    },
    active: {
        type: Boolean,
        default: true,
    },
    // Auto-enable the markdown / SVG preview when opening such a file (the eye
    // toggle stays, just defaulted on). Used by the Artifacts tab.
    previewByDefault: {
        type: Boolean,
        default: false,
    },
    displayPath: {
        type: String,
        default: null,
    },
    // Render-only artifact mode: locks the preview on and hides the source/eye
    // toggles + the Edit switch (used by the Artifacts browser view).
    renderOnly: {
        type: Boolean,
        default: false,
    },
    // When set (the session that owns this artifact), FilePane shows the artifact
    // bookmark toggle for renderable artifacts. Null outside the Artifacts context.
    artifactBookmarkSessionId: {
        type: String,
        default: null,
    },
    // True while this pane's HTML preview frame is shown inside the docking
    // overlay — raises the pooled iframe above the overlay panel.
    frameElevated: {
        type: Boolean,
        default: false,
    },
    // Force the pooled HTML-preview frame hidden even when the placeholder has
    // size. Used when a pane-local overlay must cover the preview but can't beat
    // the app-level FrameHost layer on z-index (the Files tab's mobile file-tree
    // overlay).
    frameSuppressed: {
        type: Boolean,
        default: false,
    },
})

const emit = defineEmits(['revert'])

// Stable identity for this pane's pooled HTML-preview frame (two cached panes
// must not collide).
const instanceId = useId()

const filePathLabelId = useId()
const prevChangeButtonId = useId()
const nextChangeButtonId = useId()
const markdownPreviewButtonId = useId()
const svgPreviewButtonId = useId()
const htmlPreviewButtonId = useId()
const htmlPreviewReloadButtonId = useId()
const responsiveButtonId = useId()
const selectButtonId = useId()
const previewToolsButtonId = useId()
const mermaidPreviewButtonId = useId()
const viewInFilesButtonId = useId()
const searchButtonId = useId()
const editSwitchId = useId()
const scrollTopButtonId = useId()

// Injected from SessionView: function to switch to Files tab and reveal a file.
// null when FilePane is not inside a SessionView (or no Files tab available).
const viewFileInFilesTab = inject('viewFileInFilesTab', null)

// Injected from SessionView: appends text to the session's message input.
// null when FilePane is not inside an active session — in that case the text
// selection comment widget stays disabled.
const insertTextAtCursor = inject('insertTextAtCursor', null)

// API prefix: use explicit prop when provided, otherwise project-level for drafts, session-level otherwise
const resolvedApiPrefix = computed(() => {
    if (props.apiPrefix) return props.apiPrefix
    if (props.isDraft) {
        return `/api/projects/${props.projectId}`
    }
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

const commentContext = computed(() => {
    if (!props.filePath) return null
    if (props.commitSha !== undefined) {
        // Git context
        return {
            projectId: props.projectId,
            sessionId: props.sessionId,
            subagentSessionId: '',
            source: 'git',
            sourceRef: props.commitSha ?? '',
            filePath: props.filePath,
            toolLineNum: null,
        }
    }
    // Files context
    return {
        projectId: props.projectId,
        sessionId: props.sessionId,
        subagentSessionId: '',
        source: 'files',
        sourceRef: '',
        filePath: props.filePath,
        toolLineNum: null,
    }
})

const settingsStore = useSettingsStore()

// --- CodeMirror editor instances ---
const codeEditorRef = ref(null)
const diffEditorRef = ref(null)

// --- Image pan/zoom ---
const imageRef = ref(null)
const { reset: resetZoom } = usePanZoom(imageRef)

// --- File content state ---
const currentContent = ref('')   // content currently in the editor
const loading = ref(false)       // true only for the very first load (no file displayed yet)
const switching = ref(false)     // true during file switch (editor stays visible)
const error = ref(null)
const isBinary = ref(false)
const fileSize = ref(0)
const imageSrc = ref(null)       // data URI for binary image files

// Whether the editor has ever successfully displayed a file.
// Used to distinguish "initial load" (show spinner, hide editor)
// from "file switch" (keep editor visible, show subtle indicator).
const hasLoadedOnce = ref(false)

// --- Markdown preview state ---
const isMarkdownFile = computed(() => {
    if (!props.filePath) return false
    return /\.(?:md|markdown|mdown|mkd|mkdn)$/i.test(props.filePath)
})
const showMarkdownPreview = ref(false)

// --- SVG preview state ---
const isSvgFile = computed(() => {
    if (!props.filePath) return false
    return /\.svg$/i.test(props.filePath)
})
const showSvgPreview = ref(false)
// Manually managed blob URL for SVG preview (revoked on change / unmount)
let _svgBlobUrl = null
const svgPreviewUrl = computed(() => {
    // Revoke previous blob URL if any
    if (_svgBlobUrl) {
        URL.revokeObjectURL(_svgBlobUrl)
        _svgBlobUrl = null
    }
    if (!showSvgPreview.value || !isSvgFile.value || !currentContent.value) return null
    const blob = new Blob([currentContent.value], { type: 'image/svg+xml' })
    _svgBlobUrl = URL.createObjectURL(blob)
    return _svgBlobUrl
})
onBeforeUnmount(() => {
    if (_svgBlobUrl) {
        URL.revokeObjectURL(_svgBlobUrl)
        _svgBlobUrl = null
    }
})

// --- HTML preview state ---
const isHtmlFile = computed(() => {
    if (!props.filePath) return false
    return /\.html?$/i.test(props.filePath)
})
const showHtmlPreview = ref(false)
// Bumped to force the preview <iframe> to reload. The iframe renders the file
// as served from disk (the raw endpoint), not the editor buffer, so unsaved
// edits are reflected only after saving and reloading.
const htmlPreviewReloadKey = ref(0)

// URL of the raw-serving endpoint for the current file. The file path lives in
// the URL *path* (not a query param) so an <iframe> resolves a page's relative
// CSS/JS/asset references to sibling raw URLs. Project scope uses the
// project/session prefix; standalone scope (Artifacts tab) carries the
// confinement root as a base64url path segment. Used by the HTML, PDF, audio
// and video previews.
const rawFileUrl = computed(() => {
    if (!props.filePath) return null
    const trailing = encodeFilePathSegments(props.filePath)
    if (props.projectId) {
        return `${resolvedApiPrefix.value}/file-raw/${trailing}`
    }
    return `/api/file-raw/${base64UrlEncode(props.rootRestriction || '')}/${trailing}`
})

// The HTML preview adds a cache-bust token so the reload button forces a fresh
// load. Relative-asset resolution drops the query, so siblings are unaffected.
const htmlPreviewSrc = computed(() => {
    if (!isHtmlFile.value || !rawFileUrl.value) return null
    return `${rawFileUrl.value}?_=${htmlPreviewReloadKey.value}`
})

// Whether an HTML page is currently shown in the preview iframe (vs the source
// editor). Exposed so the Artifacts tab can decide to live-reload it.
const isHtmlPreviewActive = computed(() => isHtmlFile.value && showHtmlPreview.value)

// --- Mermaid preview state (.mmd / .mermaid — rendered to a pan/zoomable SVG) ---
const isMermaidFile = computed(() => /\.(?:mmd|mermaid)$/i.test(props.filePath || ''))
const showMermaidPreview = ref(false)

// Whether any preview overlay (Markdown / SVG / HTML / Mermaid) is active.
// A preview is a read-only rendering, so the editing affordances (Search,
// Edit toggle, Save/Revert) are irrelevant and hidden while it's on; only the
// preview "eye" stays reachable to toggle back to the source.
const isPreviewing = computed(() =>
    showMarkdownPreview.value || showSvgPreview.value || showHtmlPreview.value || showMermaidPreview.value
)

// --- Binary media (PDF / audio / video) — rendered straight from the raw
// endpoint, which streams with no size cap. They are binary, need no
// file-content fetch and have no source view. ---
const isPdfFile = computed(() => /\.pdf$/i.test(props.filePath || ''))
const isAudioFile = computed(() => /\.(?:mp3|wav|ogg|oga|opus|m4a|aac|flac|weba)$/i.test(props.filePath || ''))
const isVideoFile = computed(() => /\.(?:mp4|m4v|webm|ogv|mov)$/i.test(props.filePath || ''))
const isBinaryMediaFile = computed(() => isPdfFile.value || isAudioFile.value || isVideoFile.value)

// Renderable artifact = any type FilePane shows rendered (no source view).
// Images are detected after load via imageSrc (the binary-image data URI).
const isRenderableArtifact = computed(() =>
    isMarkdownFile.value || isSvgFile.value || isHtmlFile.value || isMermaidFile.value
    || isBinaryMediaFile.value || !!imageSrc.value)

// Path of the current file relative to the artifacts root (rootRestriction),
// used as the bookmark key. Null when the file is not under the root.
const relativeArtifactPath = computed(() => {
    const root = props.rootRestriction
    const fp = props.filePath
    if (!root || !fp) return null
    const prefix = root.endsWith('/') ? root : root + '/'
    return fp.startsWith(prefix) ? fp.slice(prefix.length) : null
})

const dataStore = useDataStore()
// The bookmark for the artifact currently shown (when in artifact context),
// used to surface its name next to the path and reflect the toggle state.
const artifactBookmark = computed(() =>
    props.artifactBookmarkSessionId && relativeArtifactPath.value
        ? dataStore.artifactBookmarkFor(props.artifactBookmarkSessionId, relativeArtifactPath.value)
        : null,
)
// The header's bookmark/share buttons — also driven from the command palette.
const artifactBookmarkButtonRef = ref(null)

// --- Network broker (design §9) ---------------------------------------------
// Wire the host onto the preview <iframe>: the artifact's fetch/XHR (caught by
// the injected shim) is brokered — own assets served locally, cross-origin gated
// behind the allowlist + a consent prompt, then sent to the server proxy. The
// mount/prompt wiring lives in the shared `useArtifactBroker` composable so the
// dedicated artifact page behaves identically (phase 5, design §9).
//
// The HTML preview iframe is rendered through PersistentFrame (frames/), so it
// lives in the app-level FrameHost and survives KeepAlive session switches and
// dock moves instead of reloading. previewIframeRef resolves to that live
// element for the broker (which re-binds its own load listener).
const persistentFrameRef = ref(null)
const previewIframeRef = computed(() => persistentFrameRef.value?.frameEl ?? null)
// The over-iframe overlay layer (in the host cell) to Teleport this pane's
// preview chrome into — only while the HTML preview owns a pooled frame.
const frameOverlayEl = computed(() =>
    isHtmlPreviewActive.value ? (persistentFrameRef.value?.overlayEl ?? null) : null
)

// Persist "Forever" onto the bookmark's allowlist via the REST endpoint.
async function persistBrokerAllow(url, kind) {
    const id = artifactBookmark.value?.id
    if (id == null) return
    await apiFetch(`/api/artifact-bookmarks/${id}/allowed-hosts/`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ url, kind }),
    })
}

const { brokerPrompt, onBrokerDecision } = useArtifactBroker(
    previewIframeRef,
    () =>
        isHtmlPreviewActive.value && htmlPreviewSrc.value
            ? {
                  documentUrl: new URL(htmlPreviewSrc.value, location.href).href,
                  // Getters, not snapshots: bookmarking / un-bookmarking while the
                  // preview stays open must change whether "Forever" is offered,
                  // and a dialog-side allow/deny edit must apply live.
                  getBookmarkId: () => artifactBookmark.value?.id ?? null,
                  getAllowedHosts: () => artifactBookmark.value?.allowed_hosts ?? {},
                  getDeniedHosts: () => artifactBookmark.value?.denied_hosts ?? {},
                  persistAllow: persistBrokerAllow,
                  // Silent data/ writes only when the previewed doc lives under
                  // a session's artifacts root (design 2026-08-05 §6); the
                  // project Files tab prompts.
                  inArtifactsRoot: !!props.artifactBookmarkSessionId,
                  // Human-readable location for the data-write prompt: the
                  // page's real directory on disk, not the /api/file-raw URL.
                  getDataDirLabel: () => {
                      const fp = props.filePath || ''
                      return fp.slice(0, fp.lastIndexOf('/') + 1) + 'data/'
                  },
                  // Record a prompt denial server-side (fire-and-forget).
                  onDenied: (url, kind) => {
                      const id = artifactBookmark.value?.id
                      if (id == null) return
                      apiFetch(`/api/artifact-bookmarks/${id}/network-denials/`, {
                          method: 'POST',
                          headers: { 'content-type': 'application/json' },
                          body: JSON.stringify({ url, kind }),
                      }).catch(() => {})
                  },
              }
            : null,
    [previewIframeRef, htmlPreviewSrc, isHtmlPreviewActive],
)

// --- Session context (both HTML-preview modes) -------------------------------
// Responsive + select mode only make sense inside a session: the sidebar
// slash-route Artifacts browser (ArtifactsBrowserView) is a sessionless
// cross-session viewer. `insertTextAtCursor` is provided ONLY by SessionView,
// so its presence is the exact "we're in a session tab" signal (true in both
// the Files and Artifacts tabs, false in the slash-route viewer).
const inSessionContext = computed(() => !!insertTextAtCursor)

// --- Responsive viewport (HTML preview) --------------------------------------
// Same feature as the Browser pane's responsive mode, through the shared
// frames/ViewportStage + ViewportToolbar: the preview iframe takes an exact
// CSS-pixel viewport inside a scrollable hatched canvas. Pure host-side — the
// artifact just sees a small iframe. Per-pane refs, no persistence (parity
// with the Browser pane).
const responsiveActive = ref(false)
const viewportWidth = ref(375)
const viewportHeight = ref(667)
const viewportStageRef = ref(null)

// --- Element select mode (HTML preview) --------------------------------------
// Same feature as the Browser pane's select-area mode, but with NO companion:
// the preview iframe is same-origin (file-raw on TwiCC's own host,
// allow-same-origin sandbox), so the shared picker
// (element-select/picker.js — the very module the companion runs in-page)
// runs from HERE, directly against the iframe's document.
//
// The session whose composer/draft the picked-element comment feeds: the Files
// tab passes it as sessionId, the Artifacts tab as artifactBookmarkSessionId
// (its sessionId is null — SessionView.vue). Both are inside the same
// SessionView, so insertTextAtCursor targets the right composer regardless; the
// id is only needed to attach the screenshot to that session's draft.
const composerSessionId = computed(() => props.sessionId || props.artifactBookmarkSessionId || null)
// Gate: in a session tab (inSessionContext) with a resolvable composer session.
const canSelectElement = computed(() => inSessionContext.value && !!composerSessionId.value)
const selectToolbarRef = ref(null)
const selectModeActive = ref(false)
const selectState = ref(null)
let picker = null

async function createPicker() {
    const frameEl = previewIframeRef.value
    const win = frameEl?.contentWindow
    const doc = frameEl?.contentDocument
    if (!win || !doc) return
    // Lazy import: modern-screenshot stays out of the main bundle until the
    // mode is first used.
    const { createElementPicker } = await import('../../element-select/picker')
    // Re-check after the await: the mode may have been toggled off, the iframe
    // element replaced, or the SAME element reloaded onto a new document (the
    // contentDocument identity check — a reload keeps the element) while the
    // module loaded. Building on the captured-but-dead doc would silently
    // break the mode until the next reload.
    if (
        !selectModeActive.value ||
        picker ||
        previewIframeRef.value !== frameEl ||
        frameEl.contentDocument !== doc
    )
        return
    picker = createElementPicker({
        win,
        doc,
        onState: (state) => {
            selectState.value = state
        },
    })
    picker.enable()
}

function destroyPicker() {
    picker?.destroy()
    picker = null
    selectState.value = null
}

function setSelectMode(enabled) {
    if (selectModeActive.value === enabled) return
    selectModeActive.value = enabled
    // Drop any open element comment so a stale one can't flash back on re-entry.
    elementCommentPosition.value = null
    if (enabled) createPicker()
    else destroyPicker()
}

// Toolbar relays. Named functions (not inline arrows in the template) so the
// non-reactive `picker` closure variable is always read at call time.
function selectNav(direction) {
    picker?.nav(direction)
}

function selectClear() {
    picker?.clear()
}

// Re-arm across reloads. Agent edits bump the cache-bust src, the reload
// button does too, and KeepAlive re-parenting gives the pooled iframe a fresh
// browsing context — in every case the picker's document dies; `load` on the
// live element is the one reliable signal (same pattern as
// useArtifactBroker's rebind). Unlike the Browser pane (which drops the mode
// on navigation — the page may be a different site), an artifact reload is an
// iteration on the same document: keep the mode on, selection cleared.
let pickerIframe = null
function onPickerFrameLoad() {
    destroyPicker()
    if (selectModeActive.value) createPicker()
}
watch(
    previewIframeRef,
    (iframe) => {
        if (iframe === pickerIframe) return
        pickerIframe?.removeEventListener('load', onPickerFrameLoad)
        pickerIframe = iframe ?? null
        pickerIframe?.addEventListener('load', onPickerFrameLoad)
        // New element = new browsing context: rebuild on it too.
        onPickerFrameLoad()
    },
    { flush: 'post' },
)
// A different file is a different context — drop the mode (its `load` would
// otherwise re-arm the picker on an unrelated page).
watch(() => props.filePath, () => setSelectMode(false))
watch(isHtmlPreviewActive, (active) => {
    if (!active) setSelectMode(false)
})

// --- Element comment widget (select mode) ---
// Second TextSelectionComment instance, parallel to the text-selection one
// (they share the screen, not state — opening one closes the other). The
// "quote" is the picked element's description; the screenshot switch renders
// the element itself.
const elementCommentText = ref('')
const elementCommentPosition = ref(null) // viewport anchor, or null = closed
const elementCommentSourceLabel = computed(() => {
    // "artifact" in the Artifacts context (bookmark session id set), plain
    // "file" for a repo .html in the Files tab.
    const noun = props.artifactBookmarkSessionId ? 'artifact' : 'file'
    return `from the HTML preview of the ${noun} at ${props.displayPath || props.filePath}`
})

function openElementComment() {
    const description = picker?.describe()
    const rect = selectToolbarRef.value?.$el?.getBoundingClientRect()
    if (!description || !rect) return
    const lines = []
    if (description.openingTag) lines.push(`Element: ${description.openingTag}`)
    if (description.text) lines.push(`Text: "${description.text}"`)
    lines.push(`Path: ${description.chain}`)
    closeTextSelectionComment()
    elementCommentText.value = lines.join('\n')
    elementCommentPosition.value = { top: rect.bottom, left: rect.left + rect.width / 2, above: false }
}

// Passed to the comment widget's "Include screenshot" switch; rejects cleanly
// so the switch can revert.
function captureElementScreenshot() {
    if (!picker) return Promise.reject(new Error('select mode is off'))
    return picker.capture()
}

// dataUrl → File → draft attachment (the same path a manual image upload
// takes, so provider validation / resize / caps all apply). Uses the resolved
// composer session id (NOT props.sessionId — null in the Artifacts tab).
async function attachElementScreenshot(dataUrl) {
    const sessionId = composerSessionId.value
    if (!sessionId) throw new Error('no session to attach to')
    const blob = await (await fetch(dataUrl)).blob()
    const file = new File([blob], `artifact-capture-${Date.now()}.png`, { type: 'image/png' })
    await dataStore.addAttachment(sessionId, file)
}

// The non-scrolling wrapper around whichever preview is active. Declared here
// because the floating actions below observe it (it is their offset parent
// outside the pooled-frame case); also the focus target of focusContent().
const previewWrapRef = ref(null)

// --- Floating preview actions: collapsed behind a round "tools" toggle ------
// With two or more actions offered, the floating row folds behind a single
// round screwdriver-wrench button (click to unfold) so it doesn't crowd the
// rendered page. A preview offering only Full screen (markdown, PDF, media…)
// keeps its direct button — a toggle hiding one action would just add a click.
const previewActionsExpanded = ref(false)

// Mirrors the v-if of each action button in the template (Full screen is
// always offered on an active preview).
const previewActionCount = computed(() => {
    const html = showHtmlPreview.value && isHtmlFile.value && !props.diffMode
    return (
        1 + // Full screen
        (html && htmlPreviewSrc.value && inSessionContext.value ? 1 : 0) + // responsive
        (html && htmlPreviewSrc.value && canSelectElement.value ? 1 : 0) + // select
        (html && artifactBookmark.value ? 1 : 0) // open in new tab
    )
})
const previewActionsCollapsible = computed(() => previewActionCount.value > 1)

// Fold back AND recenter on file switch — both are transient per-page state.
watch(() => props.filePath, () => {
    previewActionsExpanded.value = false
    previewActionsPos.value = null
    openUpward.value = false
    previewTooltipPlacement.value = 'left'
})

// The floating actions can overlap the rendered page; let the user drag them
// (mouse / touch) out of the way by grabbing the round tools button. Position
// is null = default (CSS top-right) or an explicit { top, left } in px within
// the actions' offset parent (the frame-overlay cell when teleported over the
// pooled HTML frame, else .file-pane-preview). Per-pane, in-memory.
const framePool = useFramePoolStore()
const previewActionsRef = ref(null)
const previewActionsPos = ref(null)
const previewActionsStyle = computed(() =>
    previewActionsPos.value
        ? { top: `${previewActionsPos.value.top}px`, left: `${previewActionsPos.value.left}px`, right: 'auto' }
        : null
)

// The unfolded actions drop as a column below the tools button, or above it
// when the button sits too low for the column to fit below (the button never
// moves — only the drop direction flips). Recomputed when the menu opens and
// after a drag. ~34px per action button is a rough row height, enough to pick
// a side; exactness doesn't matter.
const openUpward = ref(false)
// Tooltips point inward (left) so they don't run off the right edge where the
// group lives by default; flipped to the right only when the group is dragged
// too close to the container's left edge for a left tooltip to fit.
const previewTooltipPlacement = ref('left')

function computePreviewActionsGeometry() {
    const el = previewActionsRef.value
    const parent = el?.offsetParent || el?.parentElement
    if (!el || !parent) {
        openUpward.value = false
        previewTooltipPlacement.value = 'left'
        return
    }
    const r = el.getBoundingClientRect() // the tools-button box (collapsible layout)
    const pr = parent.getBoundingClientRect()
    // Vertical: drop the column up when it wouldn't fit below.
    const needed = previewActionCount.value * 34 + 8
    const spaceBelow = pr.bottom - r.bottom
    const spaceAbove = r.top - pr.top
    openUpward.value = spaceBelow < needed && spaceAbove > spaceBelow
    // Horizontal: a left tooltip needs ~140px of room on the left of the group.
    previewTooltipPlacement.value = r.left - pr.left < 140 ? 'right' : 'left'
}
// A real drag must not fire the tools button's fold/unfold click. Reset on
// each pointerdown so a drag that ends off-target (no click) can't wedge it.
let actionsDrag = null // { pointerId, startX, startY, baseLeft, baseTop, moved }
let suppressToolsClick = false

function clampActionsPos(left, top) {
    const el = previewActionsRef.value
    const parent = el?.offsetParent || el?.parentElement
    if (!el || !parent) return { left, top }
    const pr = parent.getBoundingClientRect()
    return {
        left: Math.max(0, Math.min(pr.width - el.offsetWidth, left)),
        top: Math.max(0, Math.min(pr.height - el.offsetHeight, top)),
    }
}

// A dragged position is absolute px in the offset parent, so any shrink of
// that parent can leave the group outside the visible area — unreachable, with
// no way to bring it back. Exiting full screen after dragging it to the bottom
// is the obvious case; a dock/window resize and a sub-toolbar appearing over
// the frame do it too. Re-clamp on every parent resize (one-way: shrinking then
// re-expanding does not restore the pre-clamp spot).
useResizeObserver(
    () => frameOverlayEl.value ?? previewWrapRef.value,
    () => {
        if (!previewActionsPos.value) return // still on its default CSS corner
        const el = previewActionsRef.value
        const parent = el?.offsetParent || el?.parentElement
        // A 0-sized parent is transient (frame hidden, KeepAlive detach) —
        // clamping against it would slam the group into the top-left corner.
        if (!parent || parent.clientWidth < 1 || parent.clientHeight < 1) return
        previewActionsPos.value = clampActionsPos(previewActionsPos.value.left, previewActionsPos.value.top)
        computePreviewActionsGeometry() // drop side + tooltip side may need to flip
    }
)

function onToolsPointerDown(event) {
    if (event.button != null && event.button > 0) return // left / touch / pen only
    suppressToolsClick = false
    const el = previewActionsRef.value
    const parent = el?.offsetParent || el?.parentElement
    if (!el || !parent) return
    const r = el.getBoundingClientRect()
    const pr = parent.getBoundingClientRect()
    actionsDrag = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        baseLeft: r.left - pr.left,
        baseTop: r.top - pr.top,
        moved: false,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
}

function onToolsPointerMove(event) {
    if (!actionsDrag || event.pointerId !== actionsDrag.pointerId) return
    const dx = event.clientX - actionsDrag.startX
    const dy = event.clientY - actionsDrag.startY
    if (!actionsDrag.moved) {
        if (Math.abs(dx) < 4 && Math.abs(dy) < 4) return // below the tap/drag threshold
        actionsDrag.moved = true
        // Kill pointer-events on pooled iframes for the drag (same reason as the
        // viewport handles: an iframe swallows moves the pointer crosses).
        framePool.beginDividerDrag()
    }
    previewActionsPos.value = clampActionsPos(actionsDrag.baseLeft + dx, actionsDrag.baseTop + dy)
}

function onToolsPointerUp(event) {
    if (!actionsDrag || event.pointerId !== actionsDrag.pointerId) return
    if (actionsDrag.moved) {
        framePool.endDividerDrag()
        suppressToolsClick = true // swallow the click this drag would synthesize
        computePreviewActionsGeometry() // drop side + tooltip side may need to flip
    }
    actionsDrag = null
}

function onToolsClick() {
    if (suppressToolsClick) {
        suppressToolsClick = false
        return
    }
    if (!previewActionsExpanded.value) computePreviewActionsGeometry() // decide the drop side
    previewActionsExpanded.value = !previewActionsExpanded.value
}

// --- Edit mode state ---
const isEditing = ref(false)
const isWritable = ref(false)
const saving = ref(false)
const saveError = ref(null)

const wordWrap = computed(() => settingsStore.isEditorWordWrap)
const sideBySide = computed(() => settingsStore.isDiffSideBySide)

// Auto-switch to unified when editor area is too narrow for side-by-side.
// Start at 0 so the default is unified (safe) until the first measurement arrives.
const SIDE_BY_SIDE_MIN_WIDTH = 900
const editorAreaRef = ref(null)
const editorAreaWidth = ref(0)
const widthMeasured = ref(false)
let resizeObserver = null

// Use a watcher on the template ref instead of onMounted so that the observer
// is connected as soon as the DOM element exists (handles conditional rendering).
watch(editorAreaRef, (el, _oldEl, onCleanup) => {
    resizeObserver?.disconnect()
    resizeObserver = null
    if (el) {
        resizeObserver = new ResizeObserver((entries) => {
            for (const entry of entries) {
                if (entry.contentRect.width > 0) {
                    editorAreaWidth.value = entry.contentRect.width
                    if (!widthMeasured.value) widthMeasured.value = true
                }
            }
        })
        resizeObserver.observe(el)
    }
    onCleanup(() => {
        resizeObserver?.disconnect()
        resizeObserver = null
    })
}, { flush: 'post' })

const canSideBySide = computed(() => editorAreaWidth.value > SIDE_BY_SIDE_MIN_WIDTH)
const effectiveSideBySide = computed(() => sideBySide.value && canSideBySide.value)

// Text selection comment widget: active whenever the pane is inside an active
// session — covers the markdown preview as well as the CodeMirror code/diff
// editors, all of which live under .editor-area.
//
// Firefox + CodeMirror have a known sync issue where the DOM native selection
// (`window.getSelection()`) doesn't reflect the CodeMirror internal selection
// after certain interactions (tab-switch sequences, gutter widget cycles).
// `getCmSelectionOverride()` reads the truthy selection directly from each
// active EditorView and is preferred by the composable when it returns data.
function forEachCmView(fn) {
    const ceView = codeEditorRef.value?.view
    if (ceView) fn(ceView)
    const de = diffEditorRef.value
    if (de) {
        const m = de.getModifiedView?.()
        const o = de.getOriginalView?.()
        if (m) fn(m)
        if (o) fn(o)
    }
}

function clearSourceSelection() {
    // Drop the DOM selection (covers markdown preview and the focused side effects).
    window.getSelection()?.removeAllRanges()
    // Also collapse each CodeMirror view's internal selection — focus shifts don't
    // clear it on their own, so without this the highlight would stay visible.
    forEachCmView((view) => {
        const head = view.state.selection.main.head
        view.dispatch({ selection: { anchor: head, head } })
    })
}

function getCmSelectionOverride() {
    const views = []
    const ceView = codeEditorRef.value?.view
    if (ceView) views.push(ceView)
    const de = diffEditorRef.value
    if (de) {
        const m = de.getModifiedView?.()
        const o = de.getOriginalView?.()
        if (m) views.push(m)
        if (o) views.push(o)
    }
    for (const view of views) {
        const sel = view.state.selection.main
        if (sel.empty) continue
        const text = view.state.doc.sliceString(sel.from, sel.to)
        if (!text.trim()) continue
        // Build a real DOM Range from CodeMirror's authoritative selection so we get
        // the exact bounding rect — same algorithm the native path uses, just bypassing
        // window.getSelection() (which Firefox doesn't keep in sync with CM here).
        try {
            const fromDom = view.domAtPos(sel.from)
            const toDom = view.domAtPos(sel.to)
            const range = document.createRange()
            range.setStart(fromDom.node, fromDom.offset)
            range.setEnd(toDom.node, toDom.offset)
            const rect = range.getBoundingClientRect()
            if (!rect.width && !rect.height) continue
            // Mirror the native logic: widget appears above only for backward
            // selections spanning multiple lines.
            const backward = sel.head < sel.anchor
            const above = backward && rect.height > 30
            const startLine = view.state.doc.lineAt(sel.from)
            const endLine = view.state.doc.lineAt(sel.to)
            // Trim the range at both edges so it matches what's visible in the
            // quoted excerpt:
            //  - end exactly at the start of a line → user took nothing from that line
            //  - start exactly at the end of a line → user took nothing from that line
            let lineFrom = startLine.number
            let lineTo = endLine.number
            if (lineTo > lineFrom && startLine.to === sel.from) lineFrom += 1
            if (lineTo > lineFrom && endLine.from === sel.to) lineTo -= 1
            return {
                text,
                anchor: view.dom,
                rect: { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right },
                above,
                metadata: { filePath: props.filePath, lineFrom, lineTo, quoteMode: 'code' },
            }
        } catch {
            continue
        }
    }
    return null
}

function getMarkdownPreviewMetadata(anchor) {
    // Selections inside the rendered markdown preview don't carry line numbers
    // (the source line range isn't recoverable from rendered HTML), but we can
    // still surface the file path to disambiguate the message.
    if (!anchor || !props.filePath) return null
    const el = anchor.nodeType === Node.ELEMENT_NODE ? anchor : anchor.parentElement
    if (el?.closest?.('.markdown-preview-container')) {
        // Rendered prose, not source: quote it rather than fencing it.
        return { filePath: props.filePath, quoteMode: 'quote' }
    }
    return null
}

const {
    textSelectionCommentRef,
    textSelectionText,
    textSelectionPosition,
    textSelectionMetadata,
    closeTextSelectionComment,
    refreshSelection,
} = useTextSelectionComment({
    containerRef: editorAreaRef,
    getSelectionOverride: getCmSelectionOverride,
    enrichNativeMetadata: getMarkdownPreviewMetadata,
    enabled: computed(() => !!insertTextAtCursor),
})

// Whether the editor content differs from the last saved/fetched content.
// Delegates to the CodeMirror editor's own dirty tracking.
const isDirty = computed(() => {
    if (props.diffMode) return diffEditorRef.value?.isDirty ?? false
    return codeEditorRef.value?.isDirty ?? false
})

// Extract filename from the absolute path for display in the header
const fileName = computed(() => {
    if (!props.filePath) return ''
    const parts = props.filePath.split('/')
    return parts[parts.length - 1]
})

// Whether the CodeMirror editor should be visible.
// Hidden when: no file selected, initial load (never displayed yet), binary, or error.
// In diff mode, content is passed via props so hasLoadedOnce is not relevant.
const showEditor = computed(() => {
    if (!props.filePath) return false
    if (props.diffMode) {
        // In diff mode, show if we have at least one side of the diff
        return props.originalContent !== null || props.modifiedContent !== null
    }
    if (!hasLoadedOnce.value) return false  // initial load — show spinner instead
    if (isBinary.value) return false
    if (error.value) return false
    return true
})

// Whether an image should be displayed (binary image or SVG preview)
const showImagePreview = computed(() => {
    if (imageSrc.value) return true  // binary image with data URI
    if (showSvgPreview.value && svgPreviewUrl.value) return true
    return false
})

// Whether the header toolbar should be visible.
// Hidden for binary images (no useful controls for them).
const showHeader = computed(() => {
    if (props.diffMode) return !!props.filePath
    if (isBinary.value) return false
    // Binary media (PDF, audio, video) render as a player with no source view —
    // no toolbar, like a rendered image.
    if (isBinaryMediaFile.value) return false
    // HTML previews render in an <iframe> independent of the source fetch, so
    // keep the toolbar (preview/source toggle, reload) reachable right away —
    // even if the source content hasn't loaded (or failed to load).
    if (isHtmlFile.value) return !!props.filePath
    return !!props.filePath && hasLoadedOnce.value
})

// Whether the Edit switch is currently available — mirrors the switch's own
// v-if exactly: the header is visible, the file is writable, and we're not in
// a read-only diff (commit diffs). Gates both the Alt+E shortcut and the
// command-palette entry.
const canEdit = computed(() =>
    showHeader.value && (!props.diffMode || !props.diffReadOnly) && isWritable.value
)

// Whether a full-area placeholder should be shown (loading spinner, error, non-image binary).
// These overlay on top of the editor area.
const showOverlay = computed(() => {
    if (!props.filePath) return false
    // The HTML preview <iframe> loads independently of the source fetch; never
    // cover it with the source loading spinner / fetch error placeholder.
    if (showHtmlPreview.value && isHtmlFile.value) return false
    if (loading.value) return true
    if (error.value) return true
    if (isBinary.value && !imageSrc.value) return true  // non-image binary
    return false
})

// --- Full-window preview ("expand") ----------------------------------------
// Any active preview can be blown up to a full-window overlay. It expands IN
// PLACE (a position:fixed class on the wrapper) and is never moved in the DOM:
// relocating an <iframe> reloads it, so a teleport would wipe the PDF scroll
// position, audio/video playback, etc. Staying put keeps the same nodes, so all
// of that survives untouched. (The HTML preview iframe now lives in the
// app-level FrameHost via PersistentFrame; while fullscreen its `fullscreen`
// tier makes the host position it over the expanded wrapper's box.) To let the
// fixed overlay reach beyond the content pane (whose .main-content establishes a
// containing block via container-type and sits under the split divider), we ask
// the host (ProjectView) to drop that containment and lift the pane while
// expanded — see expandPreviewHost. Available for every preview type, including
// render-only mode (the Artifacts view, no header toolbar), via a floating toggle.
const isPreviewFullscreen = ref(false)
const previewFullscreenButtonId = `preview-fullscreen-${useId()}`
const previewOpenTabButtonId = `preview-open-tab-${useId()}`

// Provided by ProjectView: toggles .main-content--preview-expanded so the
// in-place fixed overlay can cover the whole window (sidebar included). Absent
// when FilePane is used outside a project view → graceful no-op (overlay then
// stays clamped to its pane).
const expandPreviewHost = inject('expandPreviewHost', null)

// Mirrors the v-if conditions of the individual preview elements: true iff one
// of them is actually rendered. Gates both the wrapper and its toggle, so the
// expand affordance never shows over the bare code editor.
const hasActivePreview = computed(() =>
    (showMarkdownPreview.value && isMarkdownFile.value)
    || showImagePreview.value
    || (showHtmlPreview.value && isHtmlFile.value && !!htmlPreviewSrc.value && !props.diffMode)
    || (showMermaidPreview.value && isMermaidFile.value && !props.diffMode)
    || (isPdfFile.value && !!rawFileUrl.value && !props.diffMode)
    || (isAudioFile.value && !!rawFileUrl.value && !props.diffMode)
    || (isVideoFile.value && !!rawFileUrl.value && !props.diffMode),
)

function togglePreviewFullscreen() {
    isPreviewFullscreen.value = !isPreviewFullscreen.value
}

// Escape exits full-window. Capture phase + stopPropagation so it unwraps the
// overlay first, before any ancestor Escape handler (e.g. a dialog) reacts.
function onPreviewFullscreenKeydown(event) {
    if (event.key === 'Escape') {
        event.stopPropagation()
        isPreviewFullscreen.value = false
    }
}

// While expanded: signal the host (to free the overlay from its pane) and add the
// Escape listener; revert both on exit. Also collapse if the preview itself goes
// away (file switched to source/code, preview toggled off) so there is never a
// stranded fixed overlay, an expanded host, or a dangling listener.
watch(isPreviewFullscreen, (on) => {
    expandPreviewHost?.(on)
    if (on) window.addEventListener('keydown', onPreviewFullscreenKeydown, true)
    else window.removeEventListener('keydown', onPreviewFullscreenKeydown, true)
})
watch(hasActivePreview, (has) => {
    if (!has) isPreviewFullscreen.value = false
})
onBeforeUnmount(() => {
    window.removeEventListener('keydown', onPreviewFullscreenKeydown, true)
    if (isPreviewFullscreen.value) expandPreviewHost?.(false)
})

// --- Scroll-to-top (markdown preview) ---------------------------------------
// The markdown preview is the only natively-scrollable text preview
// (.markdown-preview-container). A round button pinned to the preview's
// bottom-right appears once the reader has scrolled down and jumps back to the
// top — the quick way back to the table of contents (or the document head).
// It lives in the non-scrolling .file-pane-preview wrapper (a sibling of the
// scroll container), so it stays put over the visible area instead of scrolling
// away with the content.
const markdownScrollRef = ref(null)
const showScrollTop = ref(false)

function onMarkdownScroll() {
    const el = markdownScrollRef.value
    if (el) showScrollTop.value = el.scrollTop > 200
}

// Bind/unbind the scroll listener as the markdown-preview container mounts and
// unmounts (it's behind a v-if). flush:'post' so the element exists.
watch(markdownScrollRef, (el, _oldEl, onCleanup) => {
    showScrollTop.value = false
    if (el) {
        el.addEventListener('scroll', onMarkdownScroll, { passive: true })
        onMarkdownScroll()
        onCleanup(() => el.removeEventListener('scroll', onMarkdownScroll))
    }
}, { flush: 'post' })

function scrollMarkdownToTop() {
    markdownScrollRef.value?.scrollTo({ top: 0, behavior: 'smooth' })
}

/**
 * Check if a file is writable without fetching its content.
 * Used in diff mode where content comes from props....
 */
async function checkWritable(filePath) {
    try {
        let url = `${resolvedApiPrefix.value}/file-content/?path=${encodeURIComponent(filePath)}`
        if (props.rootRestriction) url += `&root=${encodeURIComponent(props.rootRestriction)}`
        const res = await apiFetch(url)
        if (res.ok) {
            const data = await res.json()
            isWritable.value = !!data.writable
        }
    } catch {
        // Silently ignore — isWritable stays false
    }
}

/**
 * Fetch file content from the backend.
 *
 * @param {string} filePath - absolute path to fetch
 * @param {Object} [options]
 * @param {boolean} [options.isSwitch=false] - true when switching between files
 *   (editor stays visible). false for the very first load.
 */
async function fetchFileContent(filePath, { isSwitch = false } = {}) {
    if (isSwitch) {
        switching.value = true
    } else {
        loading.value = true
    }
    error.value = null
    saveError.value = null
    isWritable.value = false
    isBinary.value = false
    imageSrc.value = null

    try {
        let url = `${resolvedApiPrefix.value}/file-content/?path=${encodeURIComponent(filePath)}`
        if (props.rootRestriction) url += `&root=${encodeURIComponent(props.rootRestriction)}`
        const res = await apiFetch(url)
        const data = await res.json()

        if (!res.ok) {
            error.value = data.error || 'Failed to load file'
            currentContent.value = ''
            return
        }

        if (data.binary) {
            isBinary.value = true
            fileSize.value = data.size
            imageSrc.value = data.image_src || null
            currentContent.value = ''
            if (imageSrc.value) hasLoadedOnce.value = true
            return
        }

        if (data.error) {
            error.value = data.error
            currentContent.value = ''
            return
        }

        currentContent.value = data.content
        fileSize.value = data.size
        isWritable.value = !!data.writable
        hasLoadedOnce.value = true
        // Reset dirty state after a tick so CodeMirror has processed the new content
        nextTick(() => codeEditorRef.value?.resetDirty())
    } catch (err) {
        error.value = 'Network error: failed to load file'
        currentContent.value = ''
    } finally {
        loading.value = false
        switching.value = false
    }
}

watch(() => props.filePath, async (newPath) => {
    if (!newPath) {
        currentContent.value = ''
        error.value = null
        isBinary.value = false
        imageSrc.value = null
        // Reset edit mode when file is deselected
        isEditing.value = false
        return
    }

    resetZoom()

    // Reset edit mode and preview modes when switching files. previewByDefault
    // (Artifacts tab) opens md/svg files directly in preview; the eye toggle
    // still lets the user switch to raw.
    isEditing.value = false
    const previewOn = props.previewByDefault || props.renderOnly
    showMarkdownPreview.value = previewOn && isMarkdownFile.value
    showSvgPreview.value = previewOn && isSvgFile.value
    showHtmlPreview.value = previewOn && isHtmlFile.value
    showMermaidPreview.value = previewOn && isMermaidFile.value

    // In diff mode, content is passed via props — don't fetch.
    if (props.diffMode) {
        currentContent.value = props.modifiedContent ?? ''
        // For editable diffs (index view), check if the file is writable
        if (!props.diffReadOnly) {
            checkWritable(newPath)
        }
        return
    }

    // Binary media (PDF, audio, video) render straight from the raw endpoint;
    // skip the file-content fetch — it would read up to its 5 MB cap or error
    // on larger media, while the player streams from file-raw with no cap.
    if (isBinaryMediaFile.value) {
        currentContent.value = ''
        error.value = null
        isBinary.value = false
        loading.value = false
        switching.value = false
        return
    }

    // If we've already displayed a file before, this is a "switch" —
    // the editor stays mounted and visible while we fetch.
    await fetchFileContent(newPath, { isSwitch: hasLoadedOnce.value })
}, { immediate: true })

// In diff mode, when the parent re-fetches diff data (e.g. refresh),
// the modifiedContent prop changes. Reset edit mode and sync currentContent.
watch(() => props.modifiedContent, (newContent) => {
    if (!props.diffMode) return
    isEditing.value = false
    currentContent.value = newContent ?? ''
})

// In diff mode, when switching from read-only (commit) to editable (index)
// for the same file, filePath doesn't change so the main watch won't fire.
// Re-check writability when diffReadOnly transitions to false.
watch(() => props.diffReadOnly, (readOnly) => {
    if (!props.diffMode || readOnly || !props.filePath) return
    checkWritable(props.filePath)
})

// --- Edit mode handlers ---

function setEditing(enabled) {
    if (enabled) {
        isEditing.value = true
        showMarkdownPreview.value = false  // exit preview when entering edit mode
        showSvgPreview.value = false       // exit SVG preview when entering edit mode
        showHtmlPreview.value = false      // exit HTML preview when entering edit mode
        showMermaidPreview.value = false   // exit Mermaid preview when entering edit mode
        saveError.value = null
    } else {
        // Revert silently when leaving edit mode
        revert()
        isEditing.value = false
    }
}

function onEditToggle(event) {
    setEditing(event.target.checked)
}

// Toggle edit mode programmatically (Alt+E shortcut and command palette).
// No-op when editing isn't available for the current file/pane.
function toggleEdit() {
    if (!canEdit.value) return
    setEditing(!isEditing.value)
}

// --- Alt+E shortcut + command palette entry ---
//
// Both the global Alt+E handler (App.vue) and the palette command target the
// *active* editable pane. Only one FilePane is ever `active` at a time (the
// visible tab of the active session/project view), so guarding on
// `props.active && canEdit` selects exactly the editor the user is looking at.

const { registerCommand, unregisterCommand } = useCommandRegistry()

// Unique per instance: every FilePane would otherwise share one id and an
// unmounting/deactivating pane could unregister the active pane's command.
const toggleEditCommandId = `display.toggle-file-edit.${useId()}`
const bookmarkArtifactCommandId = `ui.bookmark-artifact.${useId()}`
const shareArtifactCommandId = `ui.share-artifact.${useId()}`

// Alt+E is dispatched as a window event by App.vue's global key handler. It
// carries `detail.handled`, which we flip so App.vue knows to swallow the key
// (and the browser's native Edit menu / macOS dead-key accent) only when we act.
function onToggleEditShortcut(event) {
    if (!props.active || !canEdit.value) return
    if (event.detail) event.detail.handled = true
    toggleEdit()
}

onMounted(() => {
    window.addEventListener('twicc:toggle-file-edit', onToggleEditShortcut)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:toggle-file-edit', onToggleEditShortcut)
    unregisterCommand(toggleEditCommandId)
    unregisterCommand(bookmarkArtifactCommandId)
    unregisterCommand(shareArtifactCommandId)
    pickerIframe?.removeEventListener('load', onPickerFrameLoad)
    pickerIframe = null
    destroyPicker()
    if (actionsDrag?.moved) framePool.endDividerDrag() // balance if unmounted mid-drag
})

// Expose the toggle in the command palette only while this is the active,
// editable pane.
watch(
    () => props.active && canEdit.value,
    (available) => {
        if (available) {
            registerCommand({
                id: toggleEditCommandId,
                label: 'Toggle File Edit Mode',
                icon: 'pen-to-square',
                category: 'display',
                toggled: () => isEditing.value,
                action: () => toggleEdit(),
            })
        } else {
            unregisterCommand(toggleEditCommandId)
        }
    },
    { immediate: true },
)

// Same for the artifact actions, which the file-path header shows as two icons.
// That header is desktop-only (`displayPath` is passed only there), and the
// button component hosts the dialog and the "bookmark first, then share" rule —
// so the commands exist exactly where the buttons do, and delegate to them.
// `null` = no artifact to act on; a boolean = there is one, already bookmarked
// or not (the dialog creates or edits accordingly, so the label follows).
watch(
    () => (props.active && artifactBookmarkButtonRef.value ? !!artifactBookmark.value : null),
    (bookmarked) => {
        if (bookmarked !== null) {
            registerCommand({
                id: bookmarkArtifactCommandId,
                label: bookmarked ? 'Edit Artifact Bookmark…' : 'Bookmark This Artifact…',
                icon: 'bookmark',
                category: 'ui',
                action: () => artifactBookmarkButtonRef.value?.openDialog(),
            })
            registerCommand({
                id: shareArtifactCommandId,
                label: 'Share This Artifact…',
                icon: 'share-nodes',
                category: 'ui',
                // No share host configured → nothing to create, like the
                // session-level share command.
                when: () => !!artifactBookmarkButtonRef.value?.isSharingEnabled(),
                action: () => artifactBookmarkButtonRef.value?.shareArtifact(),
            })
        } else {
            unregisterCommand(bookmarkArtifactCommandId)
            unregisterCommand(shareArtifactCommandId)
        }
    },
    { immediate: true },
)

// Entering a preview leaves edit mode (preview and editing are mutually
// exclusive). The preview "eye" is disabled while there are unsaved changes,
// so the buffer is always clean here — no revert needed; the saved content
// simply sits behind the (read-only) preview overlay.
function toggleMarkdownPreview() {
    showMarkdownPreview.value = !showMarkdownPreview.value
    if (showMarkdownPreview.value) isEditing.value = false
}

function toggleSvgPreview() {
    showSvgPreview.value = !showSvgPreview.value
    if (showSvgPreview.value) isEditing.value = false
}

function toggleHtmlPreview() {
    showHtmlPreview.value = !showHtmlPreview.value
    if (showHtmlPreview.value) isEditing.value = false
}

function reloadHtmlPreview() {
    htmlPreviewReloadKey.value++
}

function toggleMermaidPreview() {
    showMermaidPreview.value = !showMermaidPreview.value
    if (showMermaidPreview.value) isEditing.value = false
}

async function save() {
    if (saving.value || !props.filePath) return

    saving.value = true
    saveError.value = null

    // Use currentContent which is kept in sync via v-model (normal mode)
    // or @update:modified (diff mode).
    const content = currentContent.value

    try {
        const body = { path: props.filePath, content }
        if (props.rootRestriction) body.root = props.rootRestriction
        const res = await apiFetch(
            `${resolvedApiPrefix.value}/file-content/`,
            {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            }
        )
        const data = await res.json()

        if (!res.ok || data.error) {
            saveError.value = data.error || 'Failed to save file'
            return
        }

        // Success: reset dirty state as new baseline
        if (props.diffMode) {
            diffEditorRef.value?.resetDirty()
        } else {
            codeEditorRef.value?.resetDirty()
        }
    } catch (err) {
        saveError.value = 'Network error: failed to save file'
    } finally {
        saving.value = false
    }
}

/**
 * Revert editor content by re-fetching the file from the backend.
 * In diff mode, emits 'revert' so the parent can re-fetch the diff.
 * In normal mode, fetches fresh content directly.
 */
async function revert() {
    if (!props.filePath) return
    if (props.diffMode) {
        emit('revert')
        return
    }
    await fetchFileContent(props.filePath, { isSwitch: true })
}

function onWordWrapToggle(event) {
    settingsStore.setEditorWordWrap(event.target.checked)
}

function onSideBySideToggle(event) {
    settingsStore.setDiffSideBySide(event.target.checked)
}

/**
 * Reload the current file content from disk.
 * Safe to call at any time: skips reload in diff mode, when no file is
 * selected, or when the editor has unsaved changes (edit mode + dirty).
 */
async function reload() {
    if (props.diffMode || !props.filePath) return
    if (isEditing.value && isDirty.value) return
    await fetchFileContent(props.filePath, { isSwitch: true })
}

/**
 * Scroll the editor to a 1-based line number.
 * Delegates to the active editor (CodeEditor or DiffEditor).
 */
function scrollToLine(lineNum) {
    if (props.diffMode) {
        diffEditorRef.value?.scrollToLine(lineNum)
    } else {
        codeEditorRef.value?.scrollToLine(lineNum)
    }
}

// "View in Files tab" button (diff mode / Git context): reveal the file at the
// diff's target line — the user's cursor line in the diff, or the first changed
// line when they never clicked into it (see DiffEditor.getViewTargetLine).
function openDiffInFilesTab() {
    const lineNum = diffEditorRef.value?.getViewTargetLine?.() ?? null
    viewFileInFilesTab?.(props.filePath, { lineNum })
}

// Focus the pane's primary content for a tab activation: the CodeMirror editor when a file is shown
// as source/diff (so the user can read / scroll / navigate it with the keyboard), otherwise the
// preview — a scrollable or natively-interactive element inside it (so arrows / PageUp-Down / space
// act on the preview). Returns whether focus now lands inside our content (drives the retry pump).
const requestContentFocus = useFocusRetry()
function focusContent() {
    requestContentFocus(focusContentOnce)
}
function focusContentOnce() {
    if (showEditor.value) {
        const ed = props.diffMode ? diffEditorRef.value : codeEditorRef.value
        const root = ed?.$el
        if (!root) return null // editor not mounted yet (content still loading) — wait for it to appear
        if (root.contains(document.activeElement)) return true
        ed?.focus?.()
        return root.contains(document.activeElement)
    }
    const wrap = previewWrapRef.value
    if (!wrap) return null // preview not mounted yet (content still loading) — wait for it to appear
    if (wrap.contains(document.activeElement)) return true
    // Prefer a natively-interactive element (iframe / pdf embed / media) then a scrollable container,
    // else the wrap itself made programmatically focusable. preventScroll so focusing never jumps it.
    const target = wrap.querySelector('iframe, embed, video, audio') || firstScrollable(wrap) || wrap
    if (!/^(IFRAME|EMBED|VIDEO|AUDIO)$/.test(target.tagName) && target.tabIndex < 0) target.tabIndex = -1
    try {
        target.focus({ preventScroll: true })
    } catch {
        // Focusing can throw on a not-yet-upgraded element — benign, the pump retries.
    }
    return wrap.contains(document.activeElement)
}
function firstScrollable(root) {
    const els = [root, ...root.querySelectorAll('*')]
    return els.find((el) => {
        const oy = getComputedStyle(el).overflowY
        return (oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight
    }) || null
}

// Whether the file content is currently being fetched (initial load or file switch)
const isLoading = computed(() => loading.value || switching.value)

// Expose dirty state, reload, scrollToLine, and loading state for parent components.
// reloadHtmlPreview + isHtmlPreviewActive let the Artifacts tab live-reload a
// rendered HTML page on disk changes.
defineExpose({ isDirty, isLoading, reload, scrollToLine, reloadHtmlPreview, isHtmlPreviewActive, focusContent, frameOverlayEl })

function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// --- CodeMirror editor event handlers ---

function onEditorReady({ view }) {
    // Post-mount setup if needed
}

function onDiffReady() {
    // Post-mount setup if needed
}

function onDiffModifiedChange(newContent) {
    currentContent.value = newContent
}

// --- Search (delegates to active editor) ---

function openSearch() {
    if (props.diffMode) {
        diffEditorRef.value?.openSearch()
    } else {
        codeEditorRef.value?.openSearch()
    }
}

// --- Diff navigation (delegates to DiffEditor) ---

function goToPreviousDiff() {
    diffEditorRef.value?.goToPreviousChunk()
}

function goToNextDiff() {
    diffEditorRef.value?.goToNextChunk()
}
</script>

<template>
    <div class="file-pane">
        <!-- File path bar (desktop only, when displayPath is provided). For a
             bookmarkable artifact it also carries the bookmark name (in
             parentheses after the path) and the bookmark toggle (far right). -->
        <template v-if="displayPath">
            <div class="file-path-header">
                <span class="file-path-label" :id="filePathLabelId">{{ displayPath }}<span v-if="artifactBookmark" class="file-path-artifact-bookmark-name"> ({{ artifactBookmark.name }})</span></span>
                <AppTooltip :for="filePathLabelId">{{ displayPath }}<template v-if="artifactBookmark"> ({{ artifactBookmark.name }})</template></AppTooltip>
                <ArtifactBookmarkButton
                    v-if="artifactBookmarkSessionId && isRenderableArtifact && relativeArtifactPath"
                    ref="artifactBookmarkButtonRef"
                    class="file-path-artifact-bookmark-btn"
                    :session-id="artifactBookmarkSessionId"
                    :relative-path="relativeArtifactPath"
                    :file-abs-path="filePath"
                    :html-content="isHtmlFile ? currentContent : null"
                />
            </div>
            <wa-divider></wa-divider>
        </template>

        <!-- Header toolbar (visible once a file has been loaded) -->
        <div v-if="showHeader && !renderOnly" class="header">
            <div class="header-left">
                <wa-button
                    v-if="!isPreviewing"
                    :id="searchButtonId"
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    class="reduced-height"
                    @click="openSearch"
                >
                    <wa-icon name="magnifying-glass"></wa-icon>
                </wa-button>
                <AppTooltip :for="searchButtonId">Search in editor</AppTooltip>
                <!-- "View in Files tab" button: shown only in diff mode (Git tab context) -->
                <wa-button
                    v-if="viewFileInFilesTab && diffMode"
                    :id="viewInFilesButtonId"
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    class="reduced-height"
                    @click="openDiffInFilesTab"
                >
                    <wa-icon name="folder-open"></wa-icon>
                </wa-button>
                <AppTooltip :for="viewInFilesButtonId">View in Files tab</AppTooltip>
                <!-- Edit controls: hidden in read-only diff mode (commit diffs),
                     and while a preview is active (editing the source is a
                     separate mode from previewing the rendered result). -->
                <template v-if="(!diffMode || !diffReadOnly) && isWritable && !isPreviewing && !renderOnly">
                    <wa-switch
                        :id="editSwitchId"
                        :checked="isEditing"
                        size="small"
                        @change="onEditToggle"
                    >Edit</wa-switch>
                    <AppTooltip :for="editSwitchId">Toggle edit mode (Alt+E)</AppTooltip>
                    <template v-if="isEditing">
                        <wa-button
                            size="small"
                            variant="brand"
                            :disabled="saving || !isDirty"
                            class="reduced-height"
                            @click="save"
                        >
                            <wa-spinner v-if="saving" slot="start"></wa-spinner>
                            Save
                        </wa-button>
                        <wa-button
                            size="small"
                            variant="neutral"
                            appearance="outlined"
                            :disabled="saving"
                            class="reduced-height"
                            @click="revert"
                        >Revert</wa-button>
                    </template>
                    <wa-button
                        v-else
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        class="header-spacer reduced-height"
                    >Spacer</wa-button>
                </template>
            </div>
            <div v-if="diffMode && !isPreviewing" class="header-center">
                <div class="diff-nav-buttons">
                    <wa-button
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        class="diff-nav-button reduced-height"
                        :id="prevChangeButtonId"
                        @click="goToPreviousDiff"
                    >
                        <wa-icon name="arrow-up"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="prevChangeButtonId">Previous change</AppTooltip>
                    <wa-button
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        class="diff-nav-button reduced-height"
                        :id="nextChangeButtonId"
                        @click="goToNextDiff"
                    >
                        <wa-icon name="arrow-down"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="nextChangeButtonId">Next change</AppTooltip>
                </div>
            </div>
            <div class="header-right">
                <wa-spinner v-if="switching" class="header-spinner"></wa-spinner>
                <wa-switch
                    v-if="!isPreviewing"
                    :checked="wordWrap"
                    size="small"
                    @change="onWordWrapToggle"
                >Wrap</wa-switch>
                <wa-switch
                    v-if="diffMode && !showMarkdownPreview && canSideBySide"
                    :checked="sideBySide"
                    size="small"
                    class="diff-layout-toggle"
                    @change="onSideBySideToggle"
                >Side by side</wa-switch>
                <!-- Markdown preview toggle: shown for .md files, including while
                     editing — but disabled until unsaved changes are saved, since
                     entering the preview leaves edit mode. -->
                <wa-button
                    v-if="isMarkdownFile && !renderOnly"
                    size="small"
                    variant="neutral"
                    :appearance="showMarkdownPreview ? 'filled' : 'outlined'"
                    :disabled="isEditing && isDirty"
                    :id="markdownPreviewButtonId"
                    class="reduced-height"
                    @click="toggleMarkdownPreview"
                >
                    <wa-icon name="eye"></wa-icon>
                </wa-button>
                <AppTooltip :for="markdownPreviewButtonId">Toggle markdown preview</AppTooltip>
                <!-- SVG preview toggle: shown for .svg files, including while
                     editing — disabled until unsaved changes are saved. -->
                <wa-button
                    v-if="isSvgFile && !renderOnly"
                    size="small"
                    variant="neutral"
                    :appearance="showSvgPreview ? 'filled' : 'outlined'"
                    :disabled="isEditing && isDirty"
                    :id="svgPreviewButtonId"
                    class="reduced-height"
                    @click="toggleSvgPreview"
                >
                    <wa-icon name="eye"></wa-icon>
                </wa-button>
                <AppTooltip :for="svgPreviewButtonId">Toggle SVG preview</AppTooltip>
                <!-- HTML preview toggle: shown for .html files, including while
                     editing (disabled until saved). Excluded in diff mode (Git
                     tab): a preview of the working-tree file would not reflect the
                     diff being viewed. -->
                <wa-button
                    v-if="isHtmlFile && !diffMode && !renderOnly"
                    size="small"
                    variant="neutral"
                    :appearance="showHtmlPreview ? 'filled' : 'outlined'"
                    :disabled="isEditing && isDirty"
                    :id="htmlPreviewButtonId"
                    class="reduced-height"
                    @click="toggleHtmlPreview"
                >
                    <wa-icon name="eye"></wa-icon>
                </wa-button>
                <AppTooltip :for="htmlPreviewButtonId">Toggle HTML preview</AppTooltip>
                <!-- Reload the rendered HTML (reflects the file as saved on disk).
                     Only present while the preview is on, which implies not editing. -->
                <wa-button
                    v-if="isHtmlFile && showHtmlPreview && !diffMode"
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    :id="htmlPreviewReloadButtonId"
                    class="reduced-height"
                    @click="reloadHtmlPreview"
                >
                    <wa-icon name="arrows-rotate"></wa-icon>
                </wa-button>
                <AppTooltip :for="htmlPreviewReloadButtonId">Reload preview</AppTooltip>
                <!-- Mermaid preview toggle: shown for .mmd files, including while
                     editing (disabled until saved). Excluded in diff mode (Git tab). -->
                <wa-button
                    v-if="isMermaidFile && !diffMode && !renderOnly"
                    size="small"
                    variant="neutral"
                    :appearance="showMermaidPreview ? 'filled' : 'outlined'"
                    :disabled="isEditing && isDirty"
                    :id="mermaidPreviewButtonId"
                    class="reduced-height"
                    @click="toggleMermaidPreview"
                >
                    <wa-icon name="eye"></wa-icon>
                </wa-button>
                <AppTooltip :for="mermaidPreviewButtonId">Toggle Mermaid preview</AppTooltip>
            </div>
        </div>

        <!-- Content area: editor is always mounted once, overlays sit on top -->
        <div ref="editorAreaRef" class="editor-area">
            <!-- Preview overlays. To go full-window the wrapper expands IN PLACE
                 (position:fixed) — it is never teleported, because moving a media
                 element (PDF, audio, video, image/Mermaid pan-zoom) in the DOM
                 reloads/resets it. The HTML preview iframe is the exception: it
                 lives in the app-level FrameHost (via PersistentFrame) precisely
                 so it survives detaches/moves, and its `fullscreen` tier tracks
                 this expanded box. ProjectView drops .main-content's container-type
                 and lifts its z-index while a preview is expanded (via the injected
                 setter) so the fixed overlay covers the whole window, sidebar
                 included. The floating toggle is pinned inside the wrapper,
                 reachable in both states and in render-only mode (which has no
                 header toolbar). -->
            <div
                v-if="hasActivePreview"
                ref="previewWrapRef"
                class="file-pane-preview"
                :class="{ 'file-pane-preview--fullscreen': isPreviewFullscreen }"
            >
                <!-- Markdown preview (when toggled on for .md files) -->
                <div v-if="showMarkdownPreview && isMarkdownFile" ref="markdownScrollRef" class="markdown-preview-container">
                    <MarkdownContent
                        :source="diffMode ? (modifiedContent ?? '') : currentContent"
                        :show-toolbar="false"
                        :show-toc="true"
                    />
                </div>

                <!-- Image preview (binary images or SVG preview) -->
                <div v-if="showImagePreview" class="image-preview-container">
                    <img
                        ref="imageRef"
                        :src="imageSrc || svgPreviewUrl"
                        :alt="fileName"
                        class="image-preview"
                    />
                </div>

                <!-- HTML preview (when toggled on for .html files). Rendered
                     through PersistentFrame so the iframe lives in the app-level
                     FrameHost — session switches (KeepAlive) and dock moves no
                     longer reload it. It loads the file from the raw endpoint so
                     the page's relative CSS/JS/asset references resolve to
                     sibling raw URLs. Sandboxed: scripts run but top-level
                     navigation, popups and modals are not allowed. -->
                <div v-if="showHtmlPreview && isHtmlFile && htmlPreviewSrc && !diffMode" class="html-preview-area">
                    <ViewportToolbar
                        v-if="responsiveActive"
                        v-model:width="viewportWidth"
                        v-model:height="viewportHeight"
                        @close="responsiveActive = false"
                    />
                    <SelectAreaToolbar
                        v-if="selectModeActive"
                        ref="selectToolbarRef"
                        :state="selectState"
                        @nav="selectNav"
                        @clear="selectClear"
                        @comment="openElementComment"
                        @close="setSelectMode(false)"
                    />
                    <!-- The stage wrappers render in BOTH modes (only restyled)
                         so PersistentFrame never unmounts on a mode toggle — a
                         remount would re-register the pooled iframe and reload
                         it. bodyEl is the frame's clip container: a scrolled-out
                         stage must not paint over the pane's chrome. -->
                    <ViewportStage
                        ref="viewportStageRef"
                        :active="responsiveActive"
                        v-model:width="viewportWidth"
                        v-model:height="viewportHeight"
                    >
                        <PersistentFrame
                            ref="persistentFrameRef"
                            :frame-id="`artifact-html:${instanceId}`"
                            :src="htmlPreviewSrc"
                            :remount-key="filePath"
                            :attrs="HTML_PREVIEW_FRAME_ATTRS"
                            :elevated="props.frameElevated"
                            :fullscreen="isPreviewFullscreen"
                            :suppressed="props.frameSuppressed"
                            :clip-el="viewportStageRef?.bodyEl ?? null"
                            class="html-preview"
                        />
                    </ViewportStage>
                </div>

                <!-- Network-broker consent prompt for the preview iframe (§9).
                     Teleported over the pooled HTML frame when one exists (its
                     own z-index can't beat the FrameHost layer); renders in
                     place otherwise. It's a wa-dialog (top-layer) either way. -->
                <Teleport :to="frameOverlayEl" :disabled="!frameOverlayEl">
                    <ArtifactBrokerPrompt :prompt="brokerPrompt" @decision="onBrokerDecision" />
                </Teleport>

                <!-- Mermaid preview (when toggled on for .mmd files) — rendered to a
                     pan/zoomable SVG, same in-panel interaction as a rendered image. -->
                <MermaidDiagram
                    v-if="showMermaidPreview && isMermaidFile && !diffMode"
                    :key="filePath"
                    :code="currentContent"
                />

                <!-- PDF preview — the browser's native PDF viewer in an iframe. -->
                <iframe
                    v-if="isPdfFile && rawFileUrl && !diffMode"
                    :key="filePath"
                    :src="rawFileUrl"
                    class="pdf-preview"
                    title="PDF preview"
                ></iframe>

                <!-- Audio preview — native <audio> player streaming from file-raw. -->
                <div v-if="isAudioFile && rawFileUrl && !diffMode" class="media-preview-container">
                    <audio :key="filePath" :src="rawFileUrl" controls class="audio-preview"></audio>
                </div>

                <!-- Video preview — native <video> player streaming from file-raw. -->
                <div v-if="isVideoFile && rawFileUrl && !diffMode" class="media-preview-container">
                    <video :key="filePath" :src="rawFileUrl" controls class="video-preview"></video>
                </div>

                <!-- Floating actions, top-right of the preview. With two or more
                     actions they fold behind the round "tools" toggle (see
                     previewActionsCollapsible); a single-action preview (just
                     Full screen) keeps its direct button. Teleported over the
                     pooled HTML frame when active (its z-index can't beat the
                     FrameHost layer); for every other preview type
                     frameOverlayEl is null so it renders in place, above the
                     in-pane preview, as before. -->
                <Teleport :to="frameOverlayEl" :disabled="!frameOverlayEl">
                <div ref="previewActionsRef" class="preview-actions" :style="previewActionsStyle">
                    <!-- The round "tools" toggle: the anchor (stays put) and the
                         drag handle for the whole floating group. While folded, a
                         pulsing brand dot on its corner flags that a sub-mode
                         (responsive / select) is running. Clicking unfolds the
                         actions as a column below it (or above, when the button
                         sits too low — the button itself never moves). -->
                    <template v-if="previewActionsCollapsible">
                        <span class="preview-tools-wrap">
                            <wa-button
                                :id="previewToolsButtonId"
                                class="preview-action-btn preview-tools-btn floating-over-text"
                                :class="{ 'preview-action-btn--active': previewActionsExpanded }"
                                size="small"
                                variant="neutral"
                                appearance="filled"
                                @click="onToolsClick"
                                @pointerdown="onToolsPointerDown"
                                @pointermove="onToolsPointerMove"
                                @pointerup="onToolsPointerUp"
                                @pointercancel="onToolsPointerUp"
                            >
                                <wa-icon name="screwdriver-wrench"></wa-icon>
                            </wa-button>
                            <span
                                v-if="!previewActionsExpanded && (responsiveActive || selectModeActive)"
                                class="preview-tools-dot"
                            ></span>
                        </span>
                        <AppTooltip :for="previewToolsButtonId" :placement="previewTooltipPlacement">
                            {{ previewActionsExpanded ? 'Hide tools' : 'Tools' }}
                        </AppTooltip>
                    </template>
                    <!-- The actions themselves: an inline row when there's no
                         tools toggle (single-action previews), or a vertical drop
                         column anchored under/over the tools button when
                         collapsible + expanded. -->
                    <div
                        v-if="!previewActionsCollapsible || previewActionsExpanded"
                        class="preview-actions-list"
                        :class="{
                            'preview-actions-list--menu': previewActionsCollapsible,
                            'preview-actions-list--up': openUpward,
                        }"
                    >
                        <!-- Responsive-mode toggle (HTML preview, session context
                             only): exact device-size viewport, same feature as the
                             Browser pane. Hidden in the sessionless slash-route
                             Artifacts browser (inSessionContext). -->
                        <template v-if="showHtmlPreview && isHtmlFile && !diffMode && htmlPreviewSrc && inSessionContext">
                            <wa-button
                                :id="responsiveButtonId"
                                class="preview-action-btn floating-over-text"
                                :class="{ 'preview-action-btn--active': responsiveActive }"
                                size="small"
                                variant="neutral"
                                appearance="filled"
                                @click="responsiveActive = !responsiveActive"
                            >
                                <wa-icon name="mobile-screen-button"></wa-icon>
                            </wa-button>
                            <AppTooltip :for="responsiveButtonId" :placement="previewTooltipPlacement">
                                {{ responsiveActive ? 'Exit responsive mode' : 'Responsive mode — preview at a device size' }}
                            </AppTooltip>
                        </template>
                        <!-- Select-element toggle (HTML preview, composer reachable):
                             pick an element in the rendered page and hand its
                             description — with an optional screenshot — to the agent. -->
                        <template v-if="showHtmlPreview && isHtmlFile && !diffMode && htmlPreviewSrc && canSelectElement">
                            <wa-button
                                :id="selectButtonId"
                                class="preview-action-btn floating-over-text"
                                :class="{ 'preview-action-btn--active': selectModeActive }"
                                size="small"
                                variant="neutral"
                                appearance="filled"
                                @click="setSelectMode(!selectModeActive)"
                            >
                                <wa-icon name="arrow-pointer"></wa-icon>
                            </wa-button>
                            <AppTooltip :for="selectButtonId" :placement="previewTooltipPlacement">
                                {{ selectModeActive ? 'Stop selecting an element' : 'Select an element' }}
                            </AppTooltip>
                        </template>
                        <!-- A real link (wa-button renders an <a> when given href), so
                             middle-click / ctrl-click / "copy link" / "open in new tab"
                             all work. Bookmarked HTML only — the id-based dedicated URL. -->
                        <template v-if="showHtmlPreview && isHtmlFile && !diffMode && artifactBookmark">
                            <wa-button
                                :id="previewOpenTabButtonId"
                                class="preview-action-btn floating-over-text"
                                size="small"
                                variant="neutral"
                                appearance="filled"
                                :href="`/artifacts/${artifactBookmark.id}/`"
                                target="_blank"
                                rel="noopener"
                            >
                                <wa-icon name="up-right-from-square"></wa-icon>
                            </wa-button>
                            <AppTooltip :for="previewOpenTabButtonId" :placement="previewTooltipPlacement">Open in new tab</AppTooltip>
                        </template>
                        <wa-button
                            :id="previewFullscreenButtonId"
                            class="preview-action-btn floating-over-text"
                            size="small"
                            variant="neutral"
                            appearance="filled"
                            @click="togglePreviewFullscreen"
                        >
                            <wa-icon :name="isPreviewFullscreen ? 'compress' : 'expand'"></wa-icon>
                        </wa-button>
                        <AppTooltip :for="previewFullscreenButtonId" :placement="previewTooltipPlacement">
                            {{ isPreviewFullscreen ? 'Exit full screen' : 'Full screen' }}
                        </AppTooltip>
                    </div>
                </div>
                </Teleport>

                <!-- Floating "scroll to top", bottom-right of the markdown
                     preview. Fades in once scrolled down; jumps back to the top
                     (and thus the table of contents). Markdown is the only
                     natively-scrollable text preview, so it's gated to it. -->
                <Transition name="scroll-top-fade">
                    <wa-button
                        v-if="showMarkdownPreview && isMarkdownFile && showScrollTop"
                        :id="scrollTopButtonId"
                        class="preview-action-btn preview-scroll-top-btn floating-over-text"
                        size="small"
                        variant="neutral"
                        appearance="filled"
                        @click="scrollMarkdownToTop"
                    >
                        <wa-icon name="arrow-up"></wa-icon>
                    </wa-button>
                </Transition>
                <AppTooltip
                    v-if="showMarkdownPreview && isMarkdownFile && showScrollTop"
                    :for="scrollTopButtonId"
                    placement="left"
                >Scroll to top</AppTooltip>
            </div>

            <!-- CodeMirror diff editor (diff mode) -->
            <DiffEditor
                v-if="diffMode && showEditor && !isPreviewing && widthMeasured"
                ref="diffEditorRef"
                :original="originalContent ?? ''"
                :modified="currentContent"
                :file-path="filePath"
                :read-only="diffReadOnly || !isEditing"
                :word-wrap="wordWrap"
                :side-by-side="effectiveSideBySide"
                :collapse-unchanged="true"
                :comment-context="commentContext"
                @update:modified="onDiffModifiedChange"
                @save="save"
                @ready="onDiffReady"
                @cm-update="refreshSelection"
            />

            <!-- CodeMirror editor — mounted once, never destroyed on file switch -->
            <CodeEditor
                v-if="!diffMode"
                v-show="showEditor && !showMarkdownPreview && !showSvgPreview && !showHtmlPreview && !showMermaidPreview && !isBinaryMediaFile"
                ref="codeEditorRef"
                v-model="currentContent"
                :file-path="filePath"
                :read-only="!isEditing"
                :word-wrap="wordWrap"
                :line-numbers="true"
                :save-view-state="false"
                :comment-context="commentContext"
                @save="save"
                @ready="onEditorReady"
                @cm-update="refreshSelection"
            />

            <!-- Overlay: initial loading (before any file has been displayed) -->
            <div v-if="showOverlay" class="editor-overlay">
                <template v-if="loading">
                    <wa-spinner></wa-spinner>
                </template>
                <template v-else-if="error">
                    <wa-callout variant="danger" size="small">
                        {{ error }}
                    </wa-callout>
                </template>
                <template v-else-if="isBinary">
                    <wa-icon name="file-zipper" style="font-size: 1.5rem; opacity: 0.5;"></wa-icon>
                    <span>Binary file ({{ formatSize(fileSize) }}) cannot be displayed</span>
                </template>
            </div>
        </div>

        <!-- Save error message (overlays above the editor) -->
        <div v-if="saveError" class="editor-overlay">
            <wa-callout variant="danger" size="small">
                {{ saveError }}
            </wa-callout>
        </div>

        <!-- Ephemeral text selection comment widget (teleported to body to avoid overflow clipping) -->
        <Teleport to="body">
            <TextSelectionComment
                v-if="textSelectionPosition"
                ref="textSelectionCommentRef"
                :selected-text="textSelectionText"
                :position="textSelectionPosition"
                :metadata="textSelectionMetadata"
                :clear-source-selection="clearSourceSelection"
                @close="closeTextSelectionComment"
            />
        </Teleport>

        <!-- Element-select comment widget (HTML preview) — teleported to body
             like every other consumer. The "quote" is the element description;
             no source selection to clear. -->
        <Teleport to="body">
            <TextSelectionComment
                v-if="elementCommentPosition"
                :selected-text="elementCommentText"
                :position="elementCommentPosition"
                :source-label="elementCommentSourceLabel"
                subject="selected area"
                auto-expand
                :clear-source-selection="() => {}"
                :capture-screenshot="captureElementScreenshot"
                :attach-screenshot="attachElementScreenshot"
                @close="elementCommentPosition = null"
            />
        </Teleport>
    </div>
</template>

<style scoped>
.file-pane {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
}

.file-path-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-s);
    min-height: 1.5rem;
    flex-shrink: 0;
    background: var(--wa-color-surface-alt);

    & + wa-divider {
        flex-shrink: 0;
        --width: var(--divider-size);
        --spacing: 0;
    }
}

.file-path-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    flex: 1;
    /* Truncate the START of the path so the file name stays visible (rtl puts
       the ellipsis on the left; text-align: left keeps short paths normal). */
    direction: rtl;
    text-align: left;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

/* Bookmark name shown right after the path, in parentheses. */
.file-path-artifact-bookmark-name {
    color: var(--wa-color-text-normal);
}

.file-path-artifact-bookmark-btn {
    flex-shrink: 0;
}

.header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--wa-space-xs) var(--wa-space-s);
    border-bottom: var(--divider-size) solid var(--wa-color-surface-border);
    min-height: 2.25rem;
    flex-shrink: 0;
    flex-wrap: wrap;
}

.header-left {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
}

.header-center {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
}

.header-right {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.diff-nav-buttons {
    display: flex;
    gap: var(--wa-space-3xs);
    flex-shrink: 0;
}

.diff-nav-button::part(base) {
    padding: var(--wa-space-3xs) var(--wa-space-2xs);
}

.diff-layout-toggle {
    flex-shrink: 0;
}

.header-spinner {
    font-size: var(--wa-font-size-s);
    flex-shrink: 0;
}

.header-spacer {
    visibility: hidden;
    pointer-events: none;
}

.editor-area {
    flex: 1;
    position: relative;
    min-height: 0;
}

/* Preview wrapper — overlays the editor area exactly like the individual
   preview containers used to (absolute, inset 0). The inner *-preview rules
   stay absolute, now relative to this wrapper: same box. */
.file-pane-preview {
    position: absolute;
    inset: 0;
}

/* Full-window: the wrapper expands IN PLACE via position:fixed (it is never
   moved in the DOM — that would reload its media); fixed/inset cover the whole
   viewport above the app chrome, ProjectView dropping .main-content's
   containment while expanded. The opaque background fills around letterboxed
   media. */
.file-pane-preview--fullscreen {
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: var(--wa-color-surface-default, #fff);
}

/* Floating expand/compress toggle, pinned to the preview's top-right corner,
   above the preview content so it stays clickable over an iframe. Subtle at
   rest, solid on hover. */
.preview-actions {
    position: absolute;
    top: var(--wa-space-s);
    right: var(--wa-space-s);
    z-index: 2;
    display: flex;
    gap: var(--wa-space-2xs);
    /* Re-enable inside the inert frame-overlay layer when teleported over the
       pooled HTML frame (the layer is pointer-events:none by contract; no
       generic `> *` re-enable there, to avoid a specificity tie). No-op when
       rendered in place over a non-pooled preview. */
    pointer-events: auto;
}

/* The actions themselves. Inline row for a single-action preview (no tools
   toggle). When collapsible, they drop as a vertical column anchored to the
   tools button's right edge — below by default, above when `--up` (the button
   never moves; only the column flips). */
.preview-actions-list {
    display: flex;
    gap: var(--wa-space-2xs);
}
.preview-actions-list--menu {
    position: absolute;
    right: 0;
    top: calc(100% + var(--wa-space-2xs));
    flex-direction: column;
    align-items: flex-end;
}
.preview-actions-list--menu.preview-actions-list--up {
    top: auto;
    bottom: calc(100% + var(--wa-space-2xs));
    flex-direction: column-reverse;
}

/* Every one of them also carries the shared .floating-over-text class
   (styles/transcript-tokens.css): they sit over a document being read, so their
   surface is a translucent tint instead of an opaque fill. */
.preview-action-btn {
    opacity: 0.6;
    transition: opacity 0.15s ease;
}
.preview-action-btn:hover,
.file-pane-preview:hover .preview-action-btn,
/* When teleported into the pooled HTML frame's overlay layer, the hover
   target is that layer, not .file-pane-preview. */
.frame-overlay-layer:hover .preview-action-btn {
    opacity: 1;
}

/* Floating "scroll to top": a round FAB pinned to the preview's bottom-right,
   mirroring the top-right actions' subtle-at-rest / solid-on-hover look
   (inherited from .preview-action-btn). */
.preview-scroll-top-btn {
    position: absolute;
    bottom: var(--wa-space-s);
    right: var(--wa-space-s);
    z-index: 2;
    pointer-events: auto;
}
.preview-scroll-top-btn::part(base) {
    border-radius: 50%;
    aspect-ratio: 1;
    padding: 0;
}
/* Fade in/out on scroll (the rest opacity is 0.6, from .preview-action-btn). */
.scroll-top-fade-enter-active,
.scroll-top-fade-leave-active {
    transition: opacity 0.2s ease;
}
.scroll-top-fade-enter-from,
.scroll-top-fade-leave-to {
    opacity: 0 !important;
}

/* Mode toggles (responsive / select): fully opaque + brand icon while active. */
.preview-action-btn--active {
    opacity: 1;
}
.preview-action-btn--active wa-icon {
    color: var(--wa-color-brand-fill-loud);
}

/* The round "tools" toggle: a circle (square box, no inline padding — the
   flex part centers the icon). Also the drag handle for the whole floating
   row: grab cursor, and touch-action:none so a touch-drag moves it instead
   of scrolling the page. */
.preview-tools-btn {
    cursor: grab;
    touch-action: none;
}
.preview-tools-btn:active {
    cursor: grabbing;
}
.preview-tools-btn::part(base) {
    border-radius: 50%;
    aspect-ratio: 1;
    padding: 0;
}

/* Active-sub-mode indicator: a small brand dot on the folded tools button's
   top-right corner, pulsing like the live-agent robot (ProcessIndicator's
   `pulse`, 1s). The dot sits on the wrapper (not the button), so the button's
   own rest opacity doesn't dim it. */
.preview-tools-wrap {
    position: relative;
    display: inline-flex;
}

.preview-tools-dot {
    position: absolute;
    top: -1px;
    right: -1px;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--wa-color-brand-fill-loud);
    pointer-events: none;
    animation: pulse 1s ease-in-out infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}

.markdown-preview-container {
    position: absolute;
    inset: 0;
    overflow-y: auto;
    padding: var(--wa-space-m) var(--wa-space-l);
}

.image-preview-container {
    position: absolute;
    inset: 0;
    overflow: hidden;
    display: flex;
    padding: var(--wa-space-m);
}

.image-preview {
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
    touch-action: none;
    margin: auto;
}

/* The preview area stacks the mode sub-toolbars over the viewport stage
   (flex:1 via its own .viewport-body rule). */
.html-preview-area {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
}

/* Placeholder sizing only — the pooled iframe (in FrameHost) carries the
   border/background; PersistentFrame positions it over this box. */
.html-preview {
    flex: 1;
    width: 100%;
    height: 100%;
}

.pdf-preview {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    border: none;
}

.media-preview-container {
    position: absolute;
    inset: 0;
    overflow: hidden;
    display: flex;
    padding: var(--wa-space-m);
}

.audio-preview {
    margin: auto;
    width: min(540px, 100%);
}

.video-preview {
    margin: auto;
    max-width: 100%;
    max-height: 100%;
}

.editor-overlay {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 100%;
    color: var(--wa-color-neutral-500);
    font-size: var(--wa-font-size-s);
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
}
</style>
