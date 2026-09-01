<script setup>
// MessageInput.vue - Text input + send/apply controls for a session.
// Per-session agent settings (model, effort, …) live in the
// ``useSessionAgentSettings`` composable; the trigger summary and the
// popover that exposes them are rendered by AgentSettingsSummary /
// AgentSettingsPopover. This component owns the textarea, attachments,
// pickers, and the send pipeline.
import { ref, computed, watch, nextTick, useId, toRef, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { getProviderHelpers, getProviderLabel, getProviderIcon } from '../../providers'
import ProviderIcon from '../ui/ProviderIcon.vue'
import { sendWsMessage, notifyUserDraftUpdated } from '../../composables/useWebSocket'
import { useSessionAgentSettings } from '../../composables/useSessionAgentSettings'
import { ensureProjectTrust } from '../../composables/useTrustGate'
import { resolveProjectTrust } from '../../utils/trust'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'
import {
    draftMediaToMediaItem,
    mediasToSdkFormat,
    resizeImageIfNeeded,
} from '../../utils/fileUtils'
import { toast } from '../../composables/useToast'
import { useCodeCommentsStore, formatAllComments } from '../../stores/codeComments'
import { getParsedContent } from '../../utils/parsedContent'
import { generateUUID } from '../../utils/crypto'
import MediaThumbnailGroup from '../media/MediaThumbnailGroup.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import FilePickerPopup from '../files/FilePickerPopup.vue'
import CommandPickerPopup from './CommandPickerPopup.vue'
import MessageHistoryPickerPopup from './MessageHistoryPickerPopup.vue'
import MessageSnippetsBar from './MessageSnippetsBar.vue'
import MessageSnippetsDialog from './MessageSnippetsDialog.vue'
import AgentSettingsSummary from './AgentSettingsSummary.vue'
import AgentSettingsPopover from './AgentSettingsPopover.vue'
import CollapsedBar from './CollapsedBar.vue'
import HybridModeExplainer from './HybridModeExplainer.vue'
import { useMessageSnippetsStore } from '../../stores/messageSnippets'
import { useWorkspacesStore } from '../../stores/workspaces'
import { getUnavailablePlaceholders, resolveSnippetText } from '../../utils/snippetPlaceholders'

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
    projectId: {
        type: String,
        required: true
    },
    // When true, the composer shares the footer with a pending request: it stays
    // fully usable for *preparing* a message but sending is blocked — the Send /
    // Apply-settings button is hidden (replaced by a "Sending paused" indicator)
    // and the keyboard send shortcut is disabled. It also defaults to its collapsed
    // bar when the request appears (independently re-expandable). The composer's
    // collapse state stays fully its own otherwise.
    sendingLocked: {
        type: Boolean,
        default: false
    },
    sendingLockedReason: {
        type: String,
        default: 'Answer the pending request to send'
    },
    sendingLockedPresentation: {
        type: String,
        default: 'paused',
        validator: value => ['paused', 'disabled'].includes(value)
    },
    // When true, another panel (a pending request, the hybrid terminal block
    // and/or the goal bar) is rendered above the composer in the footer —
    // regardless of that panel's own expanded/collapsed state. Drives the top
    // separator hairline so the composer always reads as distinct from whatever
    // sits above it. A superset of ``sendingLocked`` (which only tracks the
    // pending request).
    hasPanelAbove: {
        type: Boolean,
        default: false
    },
    // Hybrid sessions only: the embedded terminal is closed AND needs the user
    // directly (a prompt only the CLI can show, or a blocked composer). Tints
    // the hybrid icon so the warning callout above the composer has an obvious
    // target. The callout itself carries the reason; the icon opens the terminal.
    terminalAttention: {
        type: Boolean,
        default: false
    },
    // Hybrid sessions only: whether the embedded terminal block is currently
    // visible (open, or its warning callout). When it is NOT, and the composer
    // is collapsed, the hybrid icon is reproduced on the collapsed bar so the
    // terminal stays one click away.
    terminalVisible: {
        type: Boolean,
        default: false
    }
})

const router = useRouter()
const route = useRoute()
const store = useDataStore()
const settingsStore = useSettingsStore()
const codeCommentsStore = useCodeCommentsStore()

const settings = useSessionAgentSettings(toRef(props, 'sessionId'))
const {
    selectedModel,
    selectedPermissionMode,
    selectedEffort,
    selectedThinking,
    selectedClaudeInChrome,
    selectedFastMode,
    selectedContextMax,
    activeModel,
    activePermissionMode,
    activeEffort,
    activeThinking,
    activeClaudeInChrome,
    activeFastMode,
    activeContextMax,
    isStarting,
    processState,
    isContextMaxForced,
    hasDropdownsChanged,
    hasSettingsChanged,
} = settings

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))

// `expand` fires when the user expands the composer, so the parent can reduce the
// pending request (at most one of the two footer panels is expanded at a time).
const emit = defineEmits(['needs-title', 'request-expand', 'request-collapse', 'open-terminal'])

// Get session data to check if it's a draft
const session = computed(() => store.getSession(props.sessionId))
const isDraft = computed(() => session.value?.draft === true)
const providerLabel = computed(() => getProviderLabel(session.value?.provider))
const providerIcon = computed(() => getProviderIcon(session.value?.provider))

// ── Hybrid CLI mode toggle ──────────────────────────────────────────────────
// Claude Code only, never for hidden/orchestrated sessions or subagents.
//
// Three lifecycle states the button distinguishes (see the decision tree in the
// hybrid design doc):
//   - committed  (real session, ``session.hybrid`` true)  → green, locked-in
//   - pending    (draft choice OR a staged switch on an SDK session) → orange,
//                 still reversible; applied/committed on the next Send/Apply
//   - off        (neutral)
// Switching an existing SDK session is a STARTUP-type change: the dialog only
// *stages* it locally; ``set_session_hybrid`` is fired on the next Send/Apply
// (handleSend), exactly like the staged agent settings. Re-clicking a staged
// (not-yet-committed) toggle un-stages it silently.
const isHybridAvailable = computed(() =>
    settingsStore.isClaudeHybridEnabled
    && session.value?.provider === 'claude_code'
    && !session.value?.hidden
    && !session.value?.parent_session_id
)
const isHybrid = computed(() => session.value?.hybrid === true)
const isHybridCommitted = computed(() => !isDraft.value && isHybrid.value)
// Staged is meaningless once committed (a committed session is never stagable),
// so hide any leftover local flag behind the committed state.
const isHybridStaged = computed(() => !isHybridCommitted.value && store.isHybridStaged(props.sessionId))
// Orange "pending" state: a draft chose hybrid (applied when sent), or an SDK
// session has a staged switch (applied on the next Send/Apply).
const hybridPending = computed(() => (isDraft.value && isHybrid.value) || isHybridStaged.value)

const hybridTooltipLabel = computed(() => {
    if (isHybridCommitted.value) return 'Hybrid mode (permanent) — open the terminal (Alt+Shift+T)'
    if (isDraft.value && isHybrid.value) return 'Hybrid mode on for this draft — click to turn off'
    if (isHybridStaged.value) return 'Hybrid switch staged — applies on send; click to cancel'
    return 'Turn on hybrid mode'
})

// ── Hybrid dialog (single dialog, three variants) ───────────────────────────
// Variants: 'draft-enable' | 'sdk-explainer' | 'sdk-confirm'. The explanation
// lives in a <details>, always present; only the intro text, its open state and
// the switch differ between variants. (A committed session no longer opens a
// dialog — the icon opens the embedded terminal instead.)
const hybridDialogRef = ref(null)
const hybridConfirmBtnRef = ref(null)
const hybridDialogVariant = ref(null)
// "Don't show this again" defaults OFF (opt-in): the explainer keeps showing
// until the user actively asks to silence it.
const hybridDontShowAgain = ref(false)
// Forced-choice guard: switch variants must not be dismissable by Esc / the X /
// the backdrop, so we never persist the "seen" flag without a real button press.
// wa-hide can't tell Esc from a programmatic close, so we gate on this flag.
const hybridDialogAllowClose = ref(false)

const hybridDialogHasSwitch = computed(() =>
    hybridDialogVariant.value === 'draft-enable' || hybridDialogVariant.value === 'sdk-explainer'
)
const hybridDialogDetailsOpen = computed(() => hybridDialogHasSwitch.value)
const hybridDialogForcedChoice = computed(() => hybridDialogHasSwitch.value)
const hybridDialogTitle = computed(() => {
    if (hybridDialogVariant.value === 'draft-enable') return 'Start this session in hybrid mode?'
    return 'Switch this session to hybrid mode?'
})
const hybridDialogIntro = computed(() => {
    switch (hybridDialogVariant.value) {
        case 'draft-enable':
            // Draft = reversible until sent, so no "cannot be undone" now — only
            // a heads-up that the choice locks in once the session starts.
            return 'This draft will start in hybrid mode. You can turn it back off until you send the first message — after that it\'s permanent.'
        default: // sdk-explainer / sdk-confirm
            return 'This session switches to hybrid mode on your next message — and can\'t be undone afterwards.'
    }
})
// A draft "starts" hybrid; an existing SDK session "switches" — match the title.
const hybridConfirmLabel = computed(() =>
    hybridDialogVariant.value === 'draft-enable' ? 'Start in hybrid mode' : 'Switch to hybrid mode'
)

function openHybridDialog(variant) {
    hybridDialogVariant.value = variant
    hybridDontShowAgain.value = false
    hybridDialogAllowClose.value = false
    if (hybridDialogRef.value) hybridDialogRef.value.open = true
}

function handleHybridClick() {
    const seen = settingsStore.isClaudeHybridExplainerSeen
    if (isDraft.value) {
        if (isHybrid.value) {
            store.setDraftHybrid(props.sessionId, false)          // A.2 — toggle off
        } else if (seen) {
            store.setDraftHybrid(props.sessionId, true)           // A.1.b — enable directly
        } else {
            openHybridDialog('draft-enable')                       // A.1.a — explainer
        }
        return
    }
    if (isHybridCommitted.value) {
        emit('open-terminal')                                      // B.2 — open the CLI terminal
        return
    }
    if (isHybridStaged.value) {
        store.setStagedHybrid(props.sessionId, false)              // staged → un-stage
        return
    }
    openHybridDialog(seen ? 'sdk-confirm' : 'sdk-explainer')       // B.1.b / B.1.a
}

// Alt+Shift+H / the "Toggle Hybrid Mode" command: same as clicking the hybrid
// button, but only where hybrid is actually toggleable — Claude sessions that
// aren't committed-permanent. A committed session has nothing to toggle (the
// button there just opens the terminal, which Alt+Shift+T already covers), so
// the shortcut is a no-op on it. Enable/disable a draft, stage/un-stage an SDK
// session, or open the confirm dialog — exactly as handleHybridClick decides.
function hybridToggle() {
    if (!isHybridAvailable.value || isHybridCommitted.value) return
    handleHybridClick()
}

// Persist the synced "seen" flag only when the dialog actually carries the
// switch and the user left it on. Never on the info / short-confirm variants.
function persistExplainerSeenIfNeeded() {
    if (hybridDialogHasSwitch.value && hybridDontShowAgain.value) {
        settingsStore.setClaudeHybridExplainerSeen(true)
    }
}

function closeHybridDialog() {
    hybridDialogAllowClose.value = true
    if (hybridDialogRef.value) hybridDialogRef.value.open = false
}

// Footer "Switch to hybrid": stage the choice (draft flag or SDK staged switch);
// the actual commit happens on Send/Apply for SDK sessions.
function confirmHybridDialog() {
    persistExplainerSeenIfNeeded()
    if (hybridDialogVariant.value === 'draft-enable') {
        store.setDraftHybrid(props.sessionId, true)
    } else if (hybridDialogVariant.value === 'sdk-explainer' || hybridDialogVariant.value === 'sdk-confirm') {
        store.setStagedHybrid(props.sessionId, true)
    }
    closeHybridDialog()
}

function cancelHybridDialog() {
    persistExplainerSeenIfNeeded()
    closeHybridDialog()
}

// Block Esc / X / backdrop on switch variants (the wa-hide event bubbles from
// the nested wa-switch/wa-details too — scope to the dialog's own event).
function onHybridDialogHide(event) {
    if (event.target !== hybridDialogRef.value) return
    if (hybridDialogForcedChoice.value && !hybridDialogAllowClose.value) {
        event.preventDefault()
    }
}
function onHybridDialogAfterHide(event) {
    if (event.target !== hybridDialogRef.value) return
    hybridDialogVariant.value = null
    hybridDialogAllowClose.value = false
}
// Land the keyboard on the primary (brand) footer button — Start / Switch — once
// the dialog has fully shown. Guarded against the nested wa-details/wa-switch
// bubbling their own wa-after-show up to the dialog.
function onHybridDialogAfterShow(event) {
    if (event.target !== hybridDialogRef.value) return
    hybridConfirmBtnRef.value?.focus()
}

// Provider's attachment capabilities (file types, max bytes, resize policy).
// Drives the file picker's accept attribute, the paste handler's MIME
// filter, the tooltip wording, and whether the paperclip button is even
// rendered. Defaults to "nothing accepted" when the provider is unknown
// so the surface fails closed.
const attachmentSupport = computed(() => {
    const helpers = getProviderHelpers(session.value?.provider)
    return helpers?.getAttachmentSupport() ?? {
        images: false,
        documents: false,
        maxBytes: 0,
        acceptedMimeTypes: [],
        resizeImages: false,
    }
})
const acceptedMimeTypesString = computed(() => attachmentSupport.value.acceptedMimeTypes.join(','))
const canAttachAnything = computed(() => attachmentSupport.value.images || attachmentSupport.value.documents)

// Activation chars the current provider exposes for its command picker
// (e.g. ``['/']`` for Claude Code; ``['/', '$']`` for Codex when both
// land). Drives both the typed-trigger detection in ``onInput`` and the
// snippets-bar buttons. Falls back to ``[]`` when no provider is set yet.
const commandActivationChars = computed(() => {
    const helpers = getProviderHelpers(session.value?.provider)
    return helpers?.getCommandActivationChars() ?? []
})
const attachTooltipLabel = computed(() => {
    const { images, documents } = attachmentSupport.value
    if (images && documents) return 'Attach files (images, PDF, text)'
    if (images) return 'Attach images'
    if (documents) return 'Attach files (PDF, text)'
    return 'Attachments not supported'
})

// Local state for the textarea
const messageText = ref('')
const textareaRef = ref(null)
const fileInputRef = ref(null)
const attachButtonId = useId()
const settingsButtonId = useId()
const textareaAnchorId = useId()
const hybridButtonId = useId()
const hybridButtonCollapsedId = useId()

// ── Collapse-to-a-single-line ───────────────────────────────────────────────
// The composer can grow tall (textarea up to 40dvh + snippets/comments bars),
// hiding the conversation. When it exceeds a fraction of the viewport we offer a
// floating "minimize" button that collapses it to a one-line bar, freeing the
// space for the conversation — the same flex mechanism as the pending request
// minimize. State is ephemeral and per-session (reset on session change).
const rootRef = ref(null)
const collapsed = ref(false)
// True when the composer is tall enough that offering to collapse is useful.
// Driven by a ResizeObserver on the root; frozen while collapsed (the collapsed
// bar always exposes its own restore button, so the threshold is irrelevant then,
// and observing the collapsed height would just oscillate this flag).
const isTall = ref(false)
// Show the collapse affordance once the composer passes a quarter of the viewport.
const COLLAPSE_THRESHOLD_RATIO = 0.25
const collapseButtonId = useId()
const sendingLockedId = useId()
let collapseResizeObserver = null

// Message snippets dialog
const messageSnippetsDialogRef = ref(null)

// File picker popup state (@ mention)
const filePickerRef = ref(null)
const atCursorPosition = ref(null)  // cursor position right after the '@' character (typed-trigger mode)
const fileMirroredLength = ref(0)   // length of filter text mirrored into textarea after '@' (typed-trigger mode)
const atButtonMode = ref(false)     // true when opened via snippets bar button (no trigger char inserted)
const atInsertPosition = ref(null)  // cursor position where the file path will be inserted (button mode)
let atLastCloseTime = 0             // timestamp of last close (to prevent reopen on same click)

// Command picker popup state — opens when the user either types one of the
// provider's activation chars at position 0 or clicks the matching snippets-
// bar button. The active char is the prefix the picker fetches/inserts with.
const commandPickerRef = ref(null)
const commandCursorPosition = ref(null)  // cursor position right after the activation char (typed-trigger mode)
const commandMirroredLength = ref(0)     // length of filter text mirrored into textarea after the activation char (typed-trigger mode)
const commandButtonMode = ref(false)     // true when opened via snippets bar button (no trigger char inserted)
const activeCommandChar = ref(null)      // activation char the picker is currently opened with (e.g. '/')
let commandLastCloseTime = 0             // timestamp of last close (to prevent reopen on same click)

// Message history picker popup state (! at start, or PageUp on first line)
const historyPickerRef = ref(null)
const histCursorPosition = ref(null)   // cursor position right after the '!' character (bang mode only)
const histMirroredLength = ref(0)      // length of filter text mirrored into textarea after '!' (bang mode only)
const histTriggerMode = ref(null)      // 'bang' (! trigger) or 'pageup' (PageUp on first line)
const histInsertPosition = ref(null)   // cursor position for insertion (pageup mode only)
let histLastCloseTime = 0              // timestamp of last close (to prevent reopen on same click)

// Extract the text from the optimistic user message (if any) to pass to the history picker
const optimisticMessageText = computed(() => {
    const optimistic = store.localState.optimisticMessages[props.sessionId]
    if (!optimistic) return null
    const helpers = getProviderHelpers(session.value?.provider)
    if (!helpers) return null
    const parsed = getParsedContent(optimistic)
    return helpers.extractUserMessageText(parsed)
})

// Attachments for this session
const attachments = computed(() => store.getAttachments(props.sessionId))
const attachmentCount = computed(() => store.getAttachmentCount(props.sessionId))

// Temporary tooltip shown when new files are attached
const attachTooltipText = ref('')
const showAttachTooltip = ref(false)
let attachTooltipTimer = null

watch(attachmentCount, (newCount, oldCount) => {
    if (newCount > oldCount) {
        const added = newCount - oldCount
        clearTimeout(attachTooltipTimer)
        attachTooltipText.value = `${added} file${added > 1 ? 's' : ''} attached`
        showAttachTooltip.value = true
        attachTooltipTimer = setTimeout(() => {
            showAttachTooltip.value = false
        }, 2000)
    }
})

// Convert DraftMedia objects to normalized MediaItem format for the thumbnail group
const mediaItems = computed(() => attachments.value.map(a => draftMediaToMediaItem(a)))

// Whether files are currently being processed (encoded/resized) for this session
const isProcessingFiles = computed(() => store.isProcessingAttachments(props.sessionId))

// Determine if input/button should be disabled
const isDisabled = computed(() => {
    if (!store.wsConnected) return true
    const providerHelpers = getProviderHelpers(session.value?.provider)
    if (providerHelpers && !providerHelpers.canSendMessage()) return true
    if (store.isInitialSyncInProgress) return true
    if (isProcessingFiles.value) return true
    return isStarting.value
})

// Attachments alone are a valid message on an EXISTING session: both providers
// accept a user message made only of image/document blocks (verified against the
// Claude Code and Codex SDKs — creation, resume, live turn and mid-turn steer).
// Drafts are excluded on purpose: a brand new session must carry text, which is
// what the title (and its suggestion) is derived from.
const canSendAttachmentsOnly = computed(() => !isDraft.value && attachmentCount.value > 0)

// Button label based on process state and settings changes
// On drafts, the button is always "Send" since there's no process to apply settings to.
// Whether the (non-draft) session has unapplied changes that "Apply settings"
// would commit: a staged agent-settings change and/or a staged hybrid switch.
const hasUnappliedChanges = computed(() =>
    !isDraft.value && (hasSettingsChanged.value || isHybridStaged.value)
)
// Attachments count as a message, so they keep the button on "Send" even when
// settings are staged (the send applies them on the way, like a text message).
const isSettingsOnlyButton = computed(() =>
    hasUnappliedChanges.value && !messageText.value.trim() && !canSendAttachmentsOnly.value
)
const buttonLabel = computed(() => {
    const state = processState.value?.state
    if (state === 'starting') return 'Starting...'
    if (isSettingsOnlyButton.value) return 'Apply settings'
    return 'Send'
})

// Button icon changes based on mode
const buttonIcon = computed(() => {
    if (isSettingsOnlyButton.value) return 'arrows-rotate'
    return 'paper-plane'
})

// Join activation chars with "or" (oxford-style without comma before "or"):
//   1 char  → "/"
//   2 chars → "/ or $"
//   3+      → "/, $ or @"
function formatCharList(items) {
    if (items.length <= 1) return items.join('')
    const last = items[items.length - 1]
    const rest = items.slice(0, -1)
    return `${rest.join(', ')} or ${last}`
}

// Placeholder text based on process state. Provider-specific parts come
// from the helpers: ``getCommandActivationChars()`` drives the "At start:"
// hint; ``getPlaceholderAssistantTurnNote()`` controls the optional warning
// shown during ``assistant_turn``.
const placeholderText = computed(() => {
    const helpers = getProviderHelpers(session.value?.provider)
    const state = processState.value?.state

    if (state === 'starting') {
        return `Starting ${providerLabel.value} process...`
    }

    if (state === 'assistant_turn') {
        const base = `You can send a message now. ${providerLabel.value} will receive it as soon as possible (while working or after).`
        const note = helpers?.getPlaceholderAssistantTurnNote()
        return note ? `${base} ${note}` : base
    }

    const chars = helpers?.getCommandActivationChars() ?? []
    const historyHint = !isDraft.value
        ? (settingsStore.isTouchDevice ? '! = message history' : '! and Up/PageUp = message history')
        : null

    const atStartItems = []
    if (chars.length > 0) atStartItems.push(`${formatCharList(chars)} = commands`)
    if (historyHint) atStartItems.push(historyHint)

    const segments = []
    if (atStartItems.length > 0) segments.push(`At start: ${atStartItems.join(', ')}`)
    segments.push('Anywhere: @ = file paths')

    let text = `Shortcuts: ${segments.join('; ')}`
    if (!settingsStore.isTouchDevice) {
        const keys = settingsStore.isMac ? '⌘↵ or Ctrl↵' : 'Ctrl↵ or Meta↵'
        text += `, ${keys} to send`
    }
    return text
})

// Restore draft message when session changes
watch(() => props.sessionId, async (newId) => {
    // Collapse state is owned by the footer accordion (SessionItemsList), which
    // resets it on session switch — nothing to do here.
    const draft = store.getDraftMessage(newId)
    messageText.value = draft?.message || ''
    // Adjust textarea height after the DOM updates with restored content
    await nextTick()
    if (textareaRef.value?.updateComplete) {
        await textareaRef.value.updateComplete
    }
    adjustTextareaHeight()
}, { immediate: true })

// Also restore draft when it arrives after hydration (initial page load)
// This handles the race condition where the component mounts before IndexedDB is loaded
watch(
    () => store.getDraftMessage(props.sessionId),
    async (draft) => {
        // Only restore if textarea is still empty (don't overwrite user typing)
        if (!messageText.value && draft?.message) {
            messageText.value = draft.message
            // Adjust textarea height after the DOM updates with restored content
            await nextTick()
            if (textareaRef.value?.updateComplete) {
                await textareaRef.value.updateComplete
            }
            adjustTextareaHeight()
        }
    }
)

// Programmatic appends (Peer delivery) land in the store draft while this
// composer may already be mounted with text — and the draft watcher above
// ignores non-empty textareas on purpose. The append signal is the explicit
// path around that guard: it only ever fires for a deliberate append, so an
// unconditional resync cannot clobber user typing with stale data.
watch(
    () => store.getDraftAppendSignal(props.sessionId),
    async () => {
        messageText.value = store.getDraftMessage(props.sessionId)?.message || ''
        // Adjust textarea height after the DOM updates with the new content
        await nextTick()
        if (textareaRef.value?.updateComplete) {
            await textareaRef.value.updateComplete
        }
        adjustTextareaHeight()
    }
)

// Save draft message on each keystroke (debounced in store)
watch(messageText, (newText) => {
    store.setDraftMessage(props.sessionId, newText)
})

// Autofocus textarea for draft sessions (only once)
const hasAutoFocused = ref(false)

// Watch both isDraft and textareaRef - focus when both are ready
watch([isDraft, textareaRef], async ([isDraftSession, textarea]) => {
    if (isDraftSession && !hasAutoFocused.value && textarea) {
        hasAutoFocused.value = true
        // Wait for Vue's next tick
        await nextTick()
        // Wait for the Web Component to be fully rendered (Lit's updateComplete)
        if (textarea.updateComplete) {
            await textarea.updateComplete
        }
        // Wait until the textarea is visible (offsetParent !== null).
        // When creating a new session from an empty state (no session was selected),
        // the parent components (SessionView, SessionItemsList) are mounted for the first time,
        // and the textarea may not be visible yet. An element with offsetParent === null
        // cannot receive focus.
        const maxAttempts = 20
        for (let i = 0; i < maxAttempts; i++) {
            if (textarea.offsetParent !== null) {
                break
            }
            await new Promise(resolve => requestAnimationFrame(resolve))
        }
        adjustTextareaHeight()
        textarea.focus()
    }
}, { immediate: true })

/**
 * Adjust the textarea height to fit its content.
 * Accesses the internal <textarea> inside the wa-textarea shadow DOM
 * to perform a single synchronous height reset + scrollHeight read.
 * Unlike wa-textarea's built-in resize="auto", this avoids the
 * ResizeObserver feedback loop that causes 1px jitter.
 *
 * IMPORTANT: The "height = auto" reset temporarily collapses the textarea,
 * which causes the parent flex layout to reflow. During that reflow, the
 * browser synchronously clamps the VirtualScroller's scrollTop (because the
 * scroller grows when the textarea shrinks). When the textarea is restored
 * to its previous height, the clamped scrollTop is now wrong — the scroller
 * appears to jump up. To avoid this layout thrash, we skip remeasurement
 * when content and width haven't changed since the last call.
 */
let _lastMeasuredContent = null
let _lastMeasuredWidth = null

function adjustTextareaHeight() {
    // While collapsed the textarea is display:none — measuring it reads zeroes
    // and pollutes the cache. The `collapsed` watcher / expand() re-measure once
    // it is visible again.
    if (collapsed.value) return
    const textarea = textareaRef.value?.shadowRoot?.querySelector('textarea')
    if (!textarea) return

    const currentContent = textarea.value
    const currentWidth = textarea.clientWidth

    // Skip remeasurement if content and width haven't changed and height
    // is already explicitly set. This avoids the costly height='auto' reset
    // that causes layout thrash and scroll position loss on focus events.
    if (currentContent === _lastMeasuredContent
        && currentWidth === _lastMeasuredWidth
        && textarea.style.height
        && textarea.style.height !== 'auto') {
        return
    }
    const previousContent = _lastMeasuredContent
    _lastMeasuredContent = currentContent
    _lastMeasuredWidth = currentWidth

    // Fast path for growth: if scrollHeight already exceeds clientHeight
    // (with the current explicit height still set), content has grown beyond
    // the current height. Set the new height directly — no need to reset to
    // 'auto', which would temporarily collapse the textarea and cause the
    // browser to clamp the VirtualScroller's scrollTop during the forced reflow.
    if (textarea.scrollHeight > textarea.clientHeight) {
        textarea.style.height = `${textarea.scrollHeight}px`
        return
    }

    // If content didn't shrink (same length or longer), the height can only
    // stay the same or grow — and growth was already handled by the fast path
    // above. Skip the slow path to avoid layout thrash: the height='auto' reset
    // temporarily collapses the textarea, causing the browser to clamp the
    // VirtualScroller's scrollTop during the forced reflow.
    if (previousContent !== null && currentContent.length >= previousContent.length) {
        return
    }

    // Slow path for potential shrinkage (content deleted): reset to 'auto' to
    // measure the natural scrollHeight. The reset temporarily collapses the
    // textarea, causing the VirtualScroller to grow and the browser to clamp
    // its scrollTop. Save/restore scrollTop around the measurement to prevent
    // visible scroll jumps.
    const scrollerEl = textareaRef.value?.closest('.session-items-list')?.querySelector('.virtual-scroller')
    const savedScrollTop = scrollerEl?.scrollTop

    textarea.style.height = 'auto'
    if (textarea.scrollHeight > textarea.clientHeight) {
        textarea.style.height = `${textarea.scrollHeight}px`
    }

    // Restore scrollTop — the browser will clamp it to the new valid range,
    // which is correct: if the textarea shrunk, the scroller grew, so there's
    // more content visible at the bottom and less room to scroll.
    if (scrollerEl != null && savedScrollTop != null) {
        scrollerEl.scrollTop = savedScrollTop
    }
}

// ── Collapse / restore ──────────────────────────────────────────────────────

// Friendly, state-aware label for the collapsed bar so the user knows what the
// single line is — and whether anything is waiting in it. We deliberately do NOT
// preview the text; we only signal that a message has been started.
const collapsedLabel = computed(() => {
    const hasText = messageText.value.trim().length > 0
    const count = attachmentCount.value
    const filesPart = count > 0 ? `${count} file${count > 1 ? 's' : ''} attached` : ''
    if (hasText && filesPart) return `Your message is waiting · ${filesPart}`
    if (hasText) return 'Your message is waiting'
    if (filesPart) return filesPart
    // While sending is locked by a pending request, the bar is the entry point to
    // prepare the next message, so name it for that intent.
    if (props.sendingLocked) return 'Prepare a message'
    return 'Message input'
})

// Leading icon mirrors the label state.
const collapsedIcon = computed(() => {
    if (messageText.value.trim()) return 'pen'
    if (attachmentCount.value > 0) return 'paperclip'
    return 'keyboard'
})

// Imperative show/hide, driven by the footer accordion (SessionItemsList). No
// emit, so the accordion's sync never loops back into a request.
function collapse() {
    collapsed.value = true
}
function expand() {
    if (!collapsed.value) return
    collapsed.value = false
    // The textarea was display:none while collapsed, so its measured height is
    // stale; re-measure once it is visible again.
    nextTick(adjustTextareaHeight)
}

// Put the caret at the end and focus the textarea. Retries because the caller
// may invoke this right after a cross-tab navigation (Alt+Shift+PageDown from
// another session tab): the chat panel can still be display:none on the first
// try, where focus() is a no-op — keep trying until it sticks (~0.5s budget).
function focusTextarea(retries = 12) {
    const textarea = textareaRef.value
    if (textarea) {
        textarea.focus()
        const inner = textarea.shadowRoot?.querySelector('textarea')
        if (inner) {
            const end = inner.value.length
            inner.setSelectionRange(end, end)
        }
    }
    if (document.activeElement !== textarea && retries > 0) {
        setTimeout(() => focusTextarea(retries - 1), 40)
    }
}

// Focus the composer now if already shown, else as soon as the accordion
// expands it (honored by the ``collapsed`` watcher below). Robust to the order
// in which the open request and the accordion's apply land.
let wantsFocusOnExpand = false
function requestFocus() {
    if (collapsed.value) wantsFocusOnExpand = true
    else nextTick(focusTextarea)
}

// User-driven open of the composer (bar click, focus-the-composer shortcut):
// ask the accordion to open it, then focus once shown.
function requestExpandAndFocus() {
    emit('request-expand')
    requestFocus()
}
function onCollapseRequest() {
    emit('request-collapse')
}

// Re-measure the textarea whenever it becomes visible again through a non-user
// path (the sendingLocked default above, or a session switch); user-driven
// expand() already re-measures in its own nextTick.
watch(collapsed, (nowCollapsed) => {
    if (nowCollapsed) return
    nextTick(adjustTextareaHeight)
    if (wantsFocusOnExpand) {
        wantsFocusOnExpand = false
        nextTick(focusTextarea)
    }
})

// Recompute whether the composer is tall enough to be worth collapsing.
// Only meaningful while expanded (see isTall comment): while collapsed the bar
// always exposes its restore button, and observing the collapsed height here
// would just oscillate the flag.
function recomputeIsTall() {
    if (collapsed.value || !rootRef.value) return
    const viewportHeight = window.visualViewport?.height ?? window.innerHeight
    isTall.value = rootRef.value.offsetHeight > viewportHeight * COLLAPSE_THRESHOLD_RATIO
}

onMounted(() => {
    if (!rootRef.value) return
    if (typeof ResizeObserver !== 'undefined') {
        collapseResizeObserver = new ResizeObserver(recomputeIsTall)
        collapseResizeObserver.observe(rootRef.value)
    }
    // The threshold is viewport-relative, so a window/visual-viewport resize
    // (incl. the mobile keyboard) can flip it without any composer size change.
    window.addEventListener('resize', recomputeIsTall)
    window.visualViewport?.addEventListener('resize', recomputeIsTall)
    // Any "focus the message input" action (Alt+Shift+M, command palette, tab
    // nav) routes through focusChat.js, which asks us to expand first when
    // collapsed — a hidden textarea can't take focus. expand() re-shows and
    // focuses it. The command palette also drives collapse/expand directly.
    rootRef.value.addEventListener('twicc:expand-composer', requestExpandAndFocus)
    rootRef.value.addEventListener('twicc:collapse-composer', onCollapseRequest)
})

onBeforeUnmount(() => {
    collapseResizeObserver?.disconnect()
    collapseResizeObserver = null
    window.removeEventListener('resize', recomputeIsTall)
    window.visualViewport?.removeEventListener('resize', recomputeIsTall)
    rootRef.value?.removeEventListener('twicc:expand-composer', requestExpandAndFocus)
    rootRef.value?.removeEventListener('twicc:collapse-composer', onCollapseRequest)
})

/**
 * Handle textarea input event.
 * Detects '@' insertion to trigger the file picker popup.
 * Detects a provider activation char at position 0 to trigger the command picker popup.
 * Also notifies the server that the user is actively drafting (debounced).
 */
function onInput(event) {
    const newText = event.target.value
    const oldText = messageText.value

    // Detect single character insertion
    if (newText.length === oldText.length + 1) {
        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        const cursorPos = inner?.selectionStart

        // Detect '@' to trigger file picker (only at start of text or after whitespace)
        if (!filePickerRef.value?.isOpen && cursorPos > 0 && newText[cursorPos - 1] === '@'
            && (cursorPos === 1 || /\s/.test(newText[cursorPos - 2]))) {
            atCursorPosition.value = cursorPos  // right after the '@'
            fileMirroredLength.value = 0
            nextTick(() => filePickerRef.value?.open())
        }

        // Detect any of the provider's activation chars at position 0 (first
        // character of the message) to trigger the command picker. The first
        // matching char wins (providers list them in priority order).
        if (!commandPickerRef.value?.isOpen && cursorPos === 1) {
            const triggered = commandActivationChars.value.find(c => newText[0] === c)
            if (triggered) {
                activeCommandChar.value = triggered
                commandCursorPosition.value = cursorPos  // right after the activation char
                commandMirroredLength.value = 0
                nextTick(() => commandPickerRef.value?.open())
            }
        }

        // Detect '!' at position 0 (first character of the message) to trigger message history picker
        // Skip on draft sessions — no message history to show
        if (!isDraft.value && !historyPickerRef.value?.isOpen && cursorPos === 1 && newText[0] === '!') {
            histTriggerMode.value = 'bang'
            histCursorPosition.value = cursorPos  // right after the '!'
            histMirroredLength.value = 0
            histInsertPosition.value = null
            nextTick(() => historyPickerRef.value?.open())
        }
    }

    messageText.value = newText
    adjustTextareaHeight()
    // Notify server that user is actively preparing a message (debounced)
    // This prevents auto-stop of the process due to inactivity timeout
    notifyUserDraftUpdated(props.sessionId)
}

/**
 * Update textarea content programmatically (without triggering input events).
 * Sets the value on the Vue reactive ref, the wa-textarea web component,
 * and the inner shadow DOM textarea.
 */
function updateTextareaContent(newText) {
    messageText.value = newText
    if (textareaRef.value) {
        textareaRef.value.value = newText
        const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
        if (inner) {
            inner.value = newText
        }
    }
    adjustTextareaHeight()
}

/**
 * Mirror popup filter text into the textarea at the given cursor position.
 * Replaces the previously mirrored text (tracked by mirroredLengthRef) with
 * the new filter text, keeping surrounding content intact.
 */
function mirrorFilterToTextarea(pos, mirroredLengthRef, filterText) {
    if (pos == null) return

    const currentText = messageText.value
    const before = currentText.slice(0, pos)
    const after = currentText.slice(pos + mirroredLengthRef.value)
    const newText = before + filterText + after

    mirroredLengthRef.value = filterText.length
    updateTextareaContent(newText)
}

/**
 * Handle filter text changes from the file picker popup.
 * Mirrors the typed filter text into the textarea right after the '@'.
 * In button mode, no mirroring — the filter stays inside the popup.
 */
function onFilePickerFilterChange(filterText) {
    if (atButtonMode.value) return
    mirrorFilterToTextarea(atCursorPosition.value, fileMirroredLength, filterText)
}

/**
 * Handle filter text changes from the command picker popup.
 * Mirrors the typed filter text into the textarea right after the activation char.
 * In button mode, no mirroring — the filter stays inside the popup.
 */
function onCommandPickerFilterChange(filterText) {
    if (commandButtonMode.value) return
    mirrorFilterToTextarea(commandCursorPosition.value, commandMirroredLength, filterText)
}

/**
 * Handle file selection from the file picker popup.
 * Typed-trigger mode: replaces '@' + mirrored filter with '@path '.
 * Button mode: inserts '[space?]@path ' at the memorized cursor position.
 */
async function onFilePickerSelect(relativePath) {
    if (atButtonMode.value) {
        const pos = atInsertPosition.value
        if (pos != null && pos <= messageText.value.length) {
            const before = messageText.value.slice(0, pos)
            const after = messageText.value.slice(pos)
            // Prepend a space if the preceding char is non-whitespace (so '@' parses correctly)
            const prevChar = pos > 0 ? messageText.value[pos - 1] : ''
            const needsLeadingSpace = pos > 0 && !/\s/.test(prevChar)
            const leading = needsLeadingSpace ? ' ' : ''
            const trailing = after.startsWith(' ') ? '' : ' '
            const insertion = leading + '@' + relativePath + trailing
            const newText = before + insertion + after
            messageText.value = newText

            if (textareaRef.value) {
                textareaRef.value.value = newText
                const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
                if (inner) {
                    inner.value = newText
                    const newPos = pos + insertion.length
                    inner.setSelectionRange(newPos, newPos)
                }
            }
        }
    } else {
        const pos = atCursorPosition.value
        if (pos != null && pos <= messageText.value.length) {
            const before = messageText.value.slice(0, pos)
            // Skip the mirrored filter text that was transparently inserted
            const after = messageText.value.slice(pos + fileMirroredLength.value)
            // Add a trailing space unless the text after already starts with one
            const space = after.startsWith(' ') ? '' : ' '
            const newText = before + relativePath + space + after
            messageText.value = newText

            // Force update the web component and inner textarea
            if (textareaRef.value) {
                textareaRef.value.value = newText
                const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
                if (inner) {
                    inner.value = newText
                    const newPos = pos + relativePath.length + space.length
                    inner.setSelectionRange(newPos, newPos)
                }
            }
        }
    }

    atCursorPosition.value = null
    fileMirroredLength.value = 0
    atButtonMode.value = false
    atInsertPosition.value = null
    await nextTick()
    textareaRef.value?.focus()
    adjustTextareaHeight()
}

/**
 * Handle file picker popup close (without selection).
 *
 * Returns focus to the textarea and positions the cursor after the
 * trigger character + any filter text that was mirrored.
 *
 * When the popup closes with ``payload.preserveText`` (Enter pressed in the
 * search input), the user's typed filter must end up in the textarea:
 * - char-trigger mode: the filter is already mirrored after ``@`` — nothing
 *   to insert, just restore the cursor after it.
 * - button mode: nothing was mirrored, so insert ``@<filterText>`` at the
 *   memorized cursor position (with a leading space when the previous
 *   character is non-whitespace, matching the select-flow behavior).
 */
function onFilePickerClose(payload) {
    atLastCloseTime = Date.now()
    const isButtonMode = atButtonMode.value
    const pos = atCursorPosition.value
    const mirrorLen = fileMirroredLength.value
    const buttonPos = atInsertPosition.value
    atCursorPosition.value = null
    fileMirroredLength.value = 0
    atButtonMode.value = false
    atInsertPosition.value = null

    textareaRef.value?.focus()
    const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
    if (!inner) return

    if (payload?.preserveText !== undefined && isButtonMode && buttonPos != null) {
        const filter = payload.preserveText
        const prevChar = buttonPos > 0 ? messageText.value[buttonPos - 1] : ''
        const needsLeadingSpace = buttonPos > 0 && !/\s/.test(prevChar)
        const leading = needsLeadingSpace ? ' ' : ''
        const insertion = leading + '@' + filter
        const before = messageText.value.slice(0, buttonPos)
        const after = messageText.value.slice(buttonPos)
        const newText = before + insertion + after
        messageText.value = newText
        if (textareaRef.value) textareaRef.value.value = newText
        inner.value = newText
        const newPos = buttonPos + insertion.length
        inner.setSelectionRange(newPos, newPos)
        adjustTextareaHeight()
        return
    }

    if (isButtonMode && buttonPos != null) {
        // Button mode: restore cursor to the memorized position, textarea untouched
        inner.setSelectionRange(buttonPos, buttonPos)
    } else if (pos != null) {
        const cursorTarget = pos + mirrorLen
        inner.setSelectionRange(cursorTarget, cursorTarget)
    }
}

/**
 * Handle command selection from the command picker popup.
 * Replaces the entire textarea content with the selected command text.
 * (The button is only enabled when textarea is empty, so typed-trigger and
 * button-mode behave identically here: the final textarea is just the command.)
 */
async function onCommandSelect(commandText) {
    commandCursorPosition.value = null
    commandMirroredLength.value = 0
    commandButtonMode.value = false
    activeCommandChar.value = null
    messageText.value = commandText

    // Force update the web component and inner textarea
    if (textareaRef.value) {
        textareaRef.value.value = commandText
        const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
        if (inner) {
            inner.value = commandText
            const newPos = commandText.length
            inner.setSelectionRange(newPos, newPos)
        }
    }

    await nextTick()
    textareaRef.value?.focus()
    adjustTextareaHeight()
}

/**
 * Handle command picker popup close (without selection).
 *
 * Returns focus to the textarea and positions the cursor after the
 * activation char + any filter text that was mirrored.
 *
 * When the popup closes with ``payload.preserveText`` (Enter pressed in the
 * search input with no command match), the user's typed filter must end up
 * in the textarea:
 * - char-trigger mode: ``<char><filter>`` is already mirrored — nothing to
 *   insert, just restore the cursor after it.
 * - button mode: insert ``<char><filterText>`` at position 0 (the button is
 *   only enabled when the textarea is empty, so position 0 is correct).
 */
function onCommandPickerClose(payload) {
    commandLastCloseTime = Date.now()
    const isButtonMode = commandButtonMode.value
    const pos = commandCursorPosition.value
    const mirrorLen = commandMirroredLength.value
    const activeChar = activeCommandChar.value
    commandCursorPosition.value = null
    commandMirroredLength.value = 0
    commandButtonMode.value = false
    activeCommandChar.value = null

    if (payload?.preserveText !== undefined && isButtonMode && activeChar) {
        const insertion = activeChar + payload.preserveText
        messageText.value = insertion
        if (textareaRef.value) {
            textareaRef.value.value = insertion
            const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
            if (inner) {
                inner.value = insertion
                inner.setSelectionRange(insertion.length, insertion.length)
            }
            textareaRef.value.focus()
        }
        adjustTextareaHeight()
        return
    }

    // Button mode: nothing to restore — textarea was never modified
    if (isButtonMode) {
        textareaRef.value?.focus()
        return
    }

    textareaRef.value?.focus()
    if (pos != null) {
        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        if (inner) {
            const cursorTarget = pos + mirrorLen
            inner.setSelectionRange(cursorTarget, cursorTarget)
        }
    }
}

/**
 * Handle filter text changes from the message history picker popup.
 * In bang mode, mirrors the typed filter text into the textarea right after the '!'.
 * In pageup mode, no mirroring is needed.
 */
function onHistoryPickerFilterChange(filterText) {
    if (histTriggerMode.value === 'bang') {
        mirrorFilterToTextarea(histCursorPosition.value, histMirroredLength, filterText)
    }
}

/**
 * Handle message selection from the message history picker popup.
 *
 * Bang mode ('!'): Replaces the '!' trigger character and any mirrored filter
 * text with the selected message text. Preserves surrounding textarea content.
 *
 * PageUp mode: Inserts the selected message text at the cursor position
 * where PageUp was pressed. No trigger character to remove.
 */
async function onHistoryMessageSelect(selectedText) {
    const mode = histTriggerMode.value
    const triggerPos = histCursorPosition.value
    const mirrorLen = histMirroredLength.value
    const insertPos = histInsertPosition.value

    // Reset all state
    histTriggerMode.value = null
    histCursorPosition.value = null
    histMirroredLength.value = 0
    histInsertPosition.value = null

    if (mode === 'bang' && triggerPos != null) {
        const currentContent = messageText.value
        // triggerPos is right after '!', so the '!' is at triggerPos-1
        const before = currentContent.slice(0, triggerPos - 1)
        const after = currentContent.slice(triggerPos + mirrorLen)
        const newText = before + selectedText + after
        const newCursorPos = before.length + selectedText.length

        updateTextareaContent(newText)
        await nextTick()

        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        if (inner) {
            inner.setSelectionRange(newCursorPos, newCursorPos)
        }
    } else if (mode === 'pageup' && insertPos != null) {
        const currentContent = messageText.value
        const before = currentContent.slice(0, insertPos)
        const after = currentContent.slice(insertPos)
        const newText = before + selectedText + after
        const newCursorPos = before.length + selectedText.length

        updateTextareaContent(newText)
        await nextTick()

        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        if (inner) {
            inner.setSelectionRange(newCursorPos, newCursorPos)
        }
    }

    await nextTick()
    textareaRef.value?.focus()
    adjustTextareaHeight()
}

/**
 * Handle message history picker popup close (without selection).
 *
 * Returns focus to the textarea and restores the cursor position.
 *
 * Bang mode: positions cursor after '!' + any mirrored filter text.
 * PageUp mode: restores cursor to original position.
 *
 * When the popup closes with ``payload.preserveText`` (Enter pressed in the
 * search input with no message match), the user's typed filter must end up
 * in the textarea:
 * - bang mode: ``!<filter>`` is already mirrored — nothing to insert.
 * - pageup mode: insert ``<filter>`` (no ``!``) at the memorized cursor
 *   position; the ``!`` is meaningless when the user opened the picker via
 *   PageUp or the snippets-bar button.
 */
function onHistoryPickerClose(payload) {
    histLastCloseTime = Date.now()
    const mode = histTriggerMode.value
    const pos = histCursorPosition.value
    const mirrorLen = histMirroredLength.value
    const insertPos = histInsertPosition.value

    // Reset all state
    histTriggerMode.value = null
    histCursorPosition.value = null
    histMirroredLength.value = 0
    histInsertPosition.value = null

    textareaRef.value?.focus()
    const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
    if (!inner) return

    if (payload?.preserveText !== undefined && mode === 'pageup' && insertPos != null) {
        const filter = payload.preserveText
        if (filter.length > 0) {
            const before = messageText.value.slice(0, insertPos)
            const after = messageText.value.slice(insertPos)
            const newText = before + filter + after
            messageText.value = newText
            if (textareaRef.value) textareaRef.value.value = newText
            inner.value = newText
            const newPos = insertPos + filter.length
            inner.setSelectionRange(newPos, newPos)
            adjustTextareaHeight()
        } else {
            inner.setSelectionRange(insertPos, insertPos)
        }
        return
    }

    if (mode === 'bang' && pos != null) {
        const cursorTarget = pos + mirrorLen
        inner.setSelectionRange(cursorTarget, cursorTarget)
    } else if (mode === 'pageup' && insertPos != null) {
        inner.setSelectionRange(insertPos, insertPos)
    }
}

/**
 * Handle keyboard shortcuts in textarea.
 * Cmd/Ctrl+Enter submits the message.
 * PageUp on first line opens message history picker.
 */
function onKeydown(event) {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault()
        handleSend()
        return
    }

    // PageUp on first line, or ArrowUp at position 0 → open message history picker
    // Skip on draft sessions — no message history to show
    if (!isDraft.value && (event.key === 'PageUp' || event.key === 'ArrowUp') && !historyPickerRef.value?.isOpen) {
        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        if (inner) {
            const cursorPos = inner.selectionStart
            // ArrowUp requires cursor at the very start (position 0)
            // PageUp requires cursor on the first line (no newline before cursor)
            const shouldOpen = event.key === 'ArrowUp'
                ? cursorPos === 0
                : !inner.value.slice(0, cursorPos).includes('\n')
            if (shouldOpen) {
                event.preventDefault()
                histTriggerMode.value = 'pageup'
                histInsertPosition.value = cursorPos
                histCursorPosition.value = null
                histMirroredLength.value = 0
                nextTick(() => historyPickerRef.value?.open())
            }
        }
    }
}

/**
 * Open the message history picker from the snippets bar button.
 * Uses pageup mode with cursor at position 0 (insert at start of textarea).
 */
function openHistoryFromButton() {
    // If the picker just closed (via click-outside from this same click), skip reopening
    if (Date.now() - histLastCloseTime < 300) return
    if (historyPickerRef.value?.isOpen) return
    const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
    const cursorPos = inner ? inner.selectionStart : 0
    histTriggerMode.value = 'pageup'
    histInsertPosition.value = cursorPos
    histCursorPosition.value = null
    histMirroredLength.value = 0
    nextTick(() => historyPickerRef.value?.open())
}

/**
 * Open the command picker from the snippets bar button.
 * Only available when the textarea is empty. Does NOT insert the activation
 * char in the textarea — the prefix is added only if the user actually selects
 * a command. If the popup is already open, clicking elsewhere closes it via
 * the popup's click-outside handler; the 300ms guard prevents reopening on
 * the same click.
 *
 * @param {string} char - Activation char the button was clicked for. The
 *   picker uses it as the backend ``activation_char`` query param and as the
 *   prefix it inserts with the selected command.
 */
async function openCommandFromButton(char) {
    // If the picker just closed (via click-outside from this same click), skip reopening
    if (Date.now() - commandLastCloseTime < 300) return
    if (commandPickerRef.value?.isOpen) return
    // Guard: button should already be disabled when not empty, but double-check
    if (messageText.value.length > 0) return

    activeCommandChar.value = char
    commandButtonMode.value = true
    commandCursorPosition.value = null
    commandMirroredLength.value = 0
    await nextTick()
    commandPickerRef.value?.open()
}

/**
 * Open the file picker from the snippets bar button.
 * Does NOT insert '@' in the textarea — the trigger character is added
 * only if the user actually selects a file path. The cursor position is
 * memorized so the selected path can be inserted at the right place.
 * If the popup is already open, clicking elsewhere closes it via the
 * popup's click-outside handler; the 300ms guard prevents reopening on
 * the same click.
 */
async function openAtFromButton() {
    // If the picker just closed (via click-outside from this same click), skip reopening
    if (Date.now() - atLastCloseTime < 300) return
    if (filePickerRef.value?.isOpen) return

    const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
    const cursorPos = inner ? inner.selectionStart : messageText.value.length

    atButtonMode.value = true
    atInsertPosition.value = cursorPos
    atCursorPosition.value = null
    fileMirroredLength.value = 0
    await nextTick()
    filePickerRef.value?.open()
}

/**
 * Handle paste event to capture images from clipboard.
 * Only processes image files from clipboard, and only when the active
 * provider actually accepts images.
 */
async function onPaste(event) {
    if (!attachmentSupport.value.images) return

    const items = event.clipboardData?.items
    if (!items) return

    const accepted = attachmentSupport.value.acceptedMimeTypes
    for (const item of items) {
        if (item.kind === 'file' && accepted.includes(item.type)) {
            const file = item.getAsFile()
            if (file) {
                event.preventDefault()
                await processFile(file)
                return // Process only the first image
            }
        }
    }
}

/**
 * Process and add a file as an attachment. Validation (MIME, size) is
 * performed inside ``store.addAttachment`` against the provider's
 * capabilities, so a stray drag-drop or paste on a Codex session is
 * rejected with a meaningful toast even if the picker ``accept``
 * attribute was bypassed.
 */
async function processFile(file) {
    try {
        await store.addAttachment(props.sessionId, file)
        // Notify server that user is actively preparing a message
        notifyUserDraftUpdated(props.sessionId)
    } catch (error) {
        toast.error(error.message || 'Failed to process file', {
            title: 'Cannot attach file'
        })
    }
}

/**
 * Open the file picker dialog.
 */
function openFilePicker() {
    fileInputRef.value?.click()
}

/**
 * Handle file selection from the file picker.
 */
async function onFileSelected(event) {
    const files = event.target.files
    if (!files) return

    for (const file of files) {
        await processFile(file)
    }

    // Reset input so the same file can be selected again
    event.target.value = ''
}

/**
 * Remove an attachment by index (from MediaThumbnailGroup).
 * Translates the index back to the DraftMedia id for the store.
 */
function removeAttachmentByIndex(index) {
    const attachment = attachments.value[index]
    if (attachment) {
        store.removeAttachment(props.sessionId, attachment.id)
    }
}

/**
 * Remove all attachments.
 */
function removeAllAttachments() {
    store.clearAttachmentsForSession(props.sessionId)
}

/**
 * Send the message via WebSocket.
 * Backend handles both new and existing sessions with the same message type.
 * For draft sessions with a custom title, include the title in the message.
 * For draft sessions without a title, send the message AND open the rename dialog.
 *
 * Also handles settings-only updates: when text is empty but model/permission
 * mode has changed on an active process, sends a payload with empty text so
 * the backend applies the settings via SDK methods without sending a query.
 *
 * On an existing session, attachments alone are a message: the payload then
 * carries empty text plus the image/document blocks (see canSendAttachmentsOnly).
 */
async function handleSend() {
    // Sending is locked while a pending request shares the footer: the composer
    // is for *preparing* only. Guards both the click and the keyboard shortcut.
    if (props.sendingLocked) return
    const text = messageText.value.trim()
    // Attachments alone make a real message, so they take precedence over the
    // settings-only interpretation of an empty composer.
    const attachmentsOnly = !text && canSendAttachmentsOnly.value
    // A staged hybrid switch is an unapplied change too — committed below.
    const hasStagedHybrid = isHybridStaged.value
    const isSettingsOnlyUpdate = !text && !attachmentsOnly
        && (hasSettingsChanged.value || hasStagedHybrid)

    // Need text, attachments, a settings change, or a staged hybrid switch
    if ((!text && !attachmentsOnly && !isSettingsOnlyUpdate) || isDisabled.value) return

    // Trust gate for drafts whose project is still unresolved — e.g. a draft
    // hydrated from before the trust system existed, or one created while the
    // gate could not settle the state. Settle it before the first start so the
    // dialog shows when needed and the backend clamp sees a settled state.
    // When the gate settles on trusted and the draft still carries the
    // automatic untrusted permission seed, re-seed it to the now-resolved
    // default (an explicit identical user pick is indistinguishable — rare and
    // visible in the popover, so acceptable).
    if (isDraft.value && props.projectId
        && resolveProjectTrust(props.projectId, store.projects).state == null) {
        const gate = await ensureProjectTrust(props.projectId)
        if (!gate) return // user cancelled the trust dialog → don't send
        if (gate.state === true
            && selectedPermissionMode.value === settings.providerStore.value?.defaultUntrustedPermissionMode) {
            selectedPermissionMode.value = settings.resolvedDefaults.value.permission_mode
        }
    }

    // Commit a staged hybrid switch FIRST. The WS consumer processes frames in
    // order, so the backend flips ``session.hybrid`` (killing the SDK agent)
    // before it handles the send_message, and ``_create_agent`` then launches
    // the CLI to receive it. With no message, the switch alone is applied and
    // the CLI starts on the next message — we don't send an empty settings
    // update that would launch it now; any other staged setting rides the next
    // real message.
    if (hasStagedHybrid) {
        sendWsMessage({ type: 'set_session_hybrid', session_id: props.sessionId })
        store.setStagedHybrid(props.sessionId, false)
        if (!text && !attachmentsOnly) return
    }

    // Build the message payload
    // For context_max: when the auto-force-to-1M rule is active we send 1M
    // explicitly instead of the user's null/200K choice — the UI shows
    // "Forced to 1M" so it would be inconsistent to start the process at 200K.
    const payload = {
        type: 'send_message',
        session_id: props.sessionId,
        project_id: props.projectId,
        provider: session.value?.provider,
        text: text,
        // Settings: null = use global default, explicit value = forced for this session
        permission_mode: selectedPermissionMode.value,
        selected_model: selectedModel.value,
        effort: selectedEffort.value,
        thinking_enabled: selectedThinking.value,
        claude_in_chrome: selectedClaudeInChrome.value,
        fast_mode: selectedFastMode.value,
        context_max: isContextMaxForced.value
            ? store.getEffectiveContextMax(props.sessionId, selectedModel.value ?? settings.providerStore.value?.defaultModel)
            : selectedContextMax.value,
    }

    // For draft sessions with a title, include it
    if (isDraft.value && session.value?.title) {
        payload.title = session.value.title
    }

    // Hybrid CLI mode: only meaningful at creation time (drafts). Existing
    // sessions switch through the one-way `set_session_hybrid` WS command.
    if (isDraft.value) {
        payload.hybrid = session.value?.hybrid === true
        // Dockable layout seeded on the draft (resolved project → global default) — frozen onto
        // Session.layout at creation. {} = single pane.
        payload.layout = session.value?.layout || {}
    }

    // For draft sessions without a title, open the rename dialog (non-blocking)
    // The message is still sent, allowing the agent to start working
    if (isDraft.value && !session.value?.title) {
        emit('needs-title')
    }

    // Include attachments in SDK format if any. Stored images are at
    // ``MAX_IMAGE_DIMENSION`` (2576 px, Opus 4.7's native resolution);
    // each provider's helper decides whether to ship that as-is or to
    // re-resize down for the active model — Sonnet/Haiku want 1568 px,
    // Anthropic enforces a 2000 px cap on requests with >20 images, and
    // Codex re-resizes server-side so we hand it the stored blob.
    if (attachmentCount.value > 0) {
        const medias = store.getAttachments(props.sessionId)
        const imageCount = medias.filter(m => m.type === 'image').length
        const helpersForSend = getProviderHelpers(session.value?.provider)
        const effectiveModel = selectedModel.value ?? settings.providerStore.value?.defaultModel
        const targetDim = helpersForSend?.getEffectiveImageDimension({
            model: effectiveModel,
            numImages: imageCount,
        }) ?? null
        const processedMedias = targetDim === null
            ? medias
            : await Promise.all(medias.map(async media => {
                if (media.type !== 'image') return media
                const { data, mimeType } = await resizeImageIfNeeded(
                    media.data, media.mimeType, targetDim,
                )
                if (data === media.data && mimeType === media.mimeType) return media
                return { ...media, data, mimeType }
            }))
        const { images, documents } = mediasToSdkFormat(processedMedias)
        if (images.length > 0) {
            payload.images = images
        }
        if (documents.length > 0) {
            payload.documents = documents
        }
    }

    // Correlation id echoed by the backend in every reply frame, so an error
    // can be matched back to this exact send (recovery flow).
    const requestId = generateUUID()
    payload.request_id = requestId

    const success = sendWsMessage(payload)

    if (success) {
        // Sync active values to match what was just sent to the backend.
        // This makes the "Update..." button disappear immediately.
        activeModel.value = selectedModel.value
        activePermissionMode.value = selectedPermissionMode.value
        activeEffort.value = selectedEffort.value
        activeThinking.value = selectedThinking.value
        activeClaudeInChrome.value = selectedClaudeInChrome.value
        activeFastMode.value = selectedFastMode.value
        activeContextMax.value = selectedContextMax.value

        // For settings-only updates, nothing else to clean up
        if (isSettingsOnlyUpdate) return

        // Snapshot the send (original draft-format medias, BEFORE the draft
        // is cleared below) + optimistic bubble + optimistic starting state.
        store.registerOutgoingSend(props.sessionId, props.projectId, requestId, {
            text,
            medias: attachmentCount.value > 0 ? store.getAttachments(props.sessionId) : [],
            images: payload.images,
            documents: payload.documents,
        })

        // Clear draft message from store (and IndexedDB)
        store.clearDraftMessage(props.sessionId)

        // Clear attachments from store and IndexedDB
        if (attachmentCount.value > 0) {
            await store.clearAttachmentsForSession(props.sessionId)
        }

        // Clear draft session from IndexedDB only (if this was a draft session)
        // Keep in store so session stays visible until backend confirms with session_updated
        if (isDraft.value) {
            store.deleteDraftSession(props.sessionId, { keepInStore: true })
        }

        // Clear the textarea on successful send.
        // Force-clear the Web Component's value property directly: Vue may skip
        // re-pushing "" via :value.prop if it already pushed "" on a previous send
        // (Vue's template binding deduplicates identical prop values).
        messageText.value = ''
        if (textareaRef.value) {
            // Force-clear both the Web Component property and its internal <textarea>.
            // Setting wa.value alone may be ignored by the Lit setter's dedup check
            // (if _value is already ""), and even when accepted, the Lit re-render
            // with live() can be skipped if Vue's binding already pushed the same value.
            // Directly clearing the inner textarea ensures the DOM is always updated.
            textareaRef.value.value = ''
            const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
            if (inner) inner.value = ''
            await nextTick()
            adjustTextareaHeight()
        }
    }
}

/**
 * Cancel the draft session and navigate back to project list.
 * Navigates to 'projects-all' if in All Projects mode, otherwise to 'project'.
 */
function handleCancel() {
    // Clear draft message from store and IndexedDB
    store.clearDraftMessage(props.sessionId)
    store.deleteDraftSession(props.sessionId)

    if (isAllProjectsMode.value) {
        router.push({ name: 'projects-all', query: route.query.workspace ? { workspace: route.query.workspace } : {} })
    } else {
        router.push({ name: 'project', params: { projectId: props.projectId } })
    }
}

/**
 * Reset the form to its initial state: clear textarea text and attachments,
 * and restore dropdowns to their active (server-side) values.
 */
async function handleReset() {
    const hadComposerContent = Boolean(messageText.value) || attachmentCount.value > 0
    // Clear text if any
    if (messageText.value) {
        messageText.value = ''
        store.clearDraftMessage(props.sessionId)
        if (textareaRef.value) {
            textareaRef.value.value = ''
            const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
            if (inner) inner.value = ''
            await nextTick()
            adjustTextareaHeight()
        }
    }
    // Clear attachments if any
    if (attachmentCount.value > 0) {
        store.clearAttachmentsForSession(props.sessionId)
    }
    // Reset dropdowns to their reference values (active process or DB, including null)
    if (hasDropdownsChanged.value) {
        settings.restoreSettings()
    }
    // On a touch device the tap leaves focus on the button and the keyboard stays
    // closed, so the user has to tap the textarea again to keep writing. Put the
    // caret back in the emptied textarea. Only when something was actually cleared:
    // a dropdowns-only reset must not pop the keyboard. Pointer devices keep the
    // current focus (the caret is one click away).
    if (hadComposerContent && settingsStore.isTouchDevice) {
        requestFocus()
    }
}

/**
 * Insert text at the current cursor position in the textarea.
 * If no cursor position is available, appends to the end.
 * Focuses the textarea and positions the cursor after the inserted text.
 */
function insertTextAtCursor(text, { focus = true } = {}) {
    // Collapsed: the textarea is hidden, so there is no usable caret/focus and we
    // must NOT pop the composer open — the user may be reading the conversation
    // and adding comments. Append to the draft and stay collapsed; the text is
    // there (and the collapsed label reflects it) for when they expand.
    if (collapsed.value) {
        updateTextareaContent(messageText.value + text)
        return
    }

    const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
    const current = messageText.value
    const pos = inner?.selectionStart ?? current.length

    const before = current.slice(0, pos)
    const after = current.slice(inner?.selectionEnd ?? pos)
    const newText = before + text + after

    updateTextareaContent(newText)

    // Callers with their own text entry (the floating comment widget) pass
    // focus:false so "Add to message" doesn't grab focus and pop the mobile
    // keyboard — jarring in general, and outright broken when the composer sits
    // behind an overlay (a docked Browser pane). The text still lands in the
    // draft; only the caret move + focus are skipped. The widget re-enables focus
    // in the chat pane on pointer devices only (see its focusComposerOnAdd prop).
    if (!focus) return

    // Position cursor after the inserted text and focus
    const newPos = pos + text.length
    nextTick(() => {
        const innerEl = textareaRef.value?.shadowRoot?.querySelector('textarea')
        if (innerEl) {
            innerEl.setSelectionRange(newPos, newPos)
        }
        textareaRef.value?.focus()
    })
}

// ─── Code comments: "Add all comments to message" button ─────────────────────

const sessionCommentsWithContent = computed(() =>
    codeCommentsStore.getCommentsBySession(props.projectId, props.sessionId)
        .filter(c => c.content.trim())
)

const commentsWithContentCount = computed(() => sessionCommentsWithContent.value.length)

function clearAllSessionComments() {
    codeCommentsStore.removeAllSessionComments(props.projectId, props.sessionId)
}

function addAllCommentsToMessage() {
    const comments = sessionCommentsWithContent.value
    if (comments.length === 0) return
    insertTextAtCursor(formatAllComments(comments) + '\n')
    codeCommentsStore.removeAllSessionComments(props.projectId, props.sessionId)
}

// ── Message snippets ────────────────────────────────────────────────
const messageSnippetsStore = useMessageSnippetsStore()

/** Placeholder resolution context (same shape as terminal uses). */
const placeholderContext = computed(() => {
    const s = session.value
    const pid = props.projectId
    const project = pid ? store.getProject(pid) : null
    const projectName = pid ? store.getProjectDisplayName(pid) : null
    return { session: s, project, projectName }
})

/**
 * Project id used to SELECT which snippet lists to show: in a worktree session
 * we borrow the main repository's workspaces and project-scoped snippets (a
 * worktree has none of its own). Placeholders are still resolved against the
 * real session project (see `placeholderContext`), not this one.
 */
const snippetListProjectId = computed(() => store.getMainRepoProjectId(props.projectId))

/** Workspace IDs for snippet display: active workspace, or all workspaces containing the (main repo) project. */
const snippetWorkspaceIds = computed(() => {
    const wsId = route.query.workspace
    if (wsId) return [wsId]
    if (!snippetListProjectId.value) return []
    const workspacesStore = useWorkspacesStore()
    return workspacesStore.getWorkspacesForProject(snippetListProjectId.value).map(ws => ws.id)
})

/** Snippets for this project, enriched with _disabled / _disabledReason for unresolvable placeholders. */
const snippetsForProject = computed(() => {
    const raw = snippetListProjectId.value ? messageSnippetsStore.getSnippetsForProject(snippetListProjectId.value, snippetWorkspaceIds.value) : []
    const ctx = placeholderContext.value

    return raw.map(snippet => {
        const placeholders = snippet.placeholders || []
        if (placeholders.length === 0) return snippet
        const unavailable = getUnavailablePlaceholders(placeholders, ctx)
        if (unavailable.length === 0) return snippet
        return {
            ...snippet,
            _disabled: true,
            _disabledReason: `Not available: ${unavailable.map(p => p.label).join(', ')}`,
        }
    })
})

function handleSnippetPress(snippet) {
    const placeholders = snippet.placeholders || []
    const resolved = resolveSnippetText(snippet.text, placeholders, placeholderContext.value)
    insertTextAtCursor(resolved)
}

/**
 * Touch long-press on a snippet: insert and send in one gesture, without ever
 * focusing the textarea (no on-screen keyboard, no layout resize). When the
 * send is not available the gesture degrades to a plain press, so the text
 * still lands in the composer and the user sees why nothing was sent.
 */
function handleSnippetLongPress(snippet) {
    const placeholders = snippet.placeholders || []
    const resolved = resolveSnippetText(snippet.text, placeholders, placeholderContext.value)
    // Mirrors the Send button's own disabled condition; the empty-text part of
    // it is satisfied by the insertion below, hence the `resolved` check here.
    if (props.sendingLocked || isDisabled.value || !resolved.trim()) {
        insertTextAtCursor(resolved)
        return
    }
    // `updateTextareaContent` writes `messageText` synchronously, so `handleSend`
    // already sees the inserted text.
    insertTextAtCursor(resolved, { focus: false })
    handleSend()
}

function handleSnippetDisabledPress(snippet) {
    toast(snippet._disabledReason || 'Some placeholders are not available', { variant: 'warning' })
}

function openMessageSnippetsDialog() {
    messageSnippetsDialogRef.value?.open()
}

function getSessionSetting(key) {
    const ref_ = settings.SELECTED_REFS[key]
    return ref_ ? ref_.value : null
}

function setSessionSetting(key, value) {
    const ref_ = settings.SELECTED_REFS[key]
    if (ref_) ref_.value = value
}

function getSessionGateState() {
    const helpers = settings.providerHelpers.value
    const model = settings.effectiveModel.value
    return {
        isStarting: settings.isStarting.value,
        isContextMaxForced: settings.isContextMaxForced.value,
        isContextMaxForcedByModel: !helpers?.modelSupports1m?.(model),
        effectiveModel: model,
    }
}

defineExpose({ insertTextAtCursor, getSessionSetting, setSessionSetting, getSessionGateState, collapse, expand, requestFocus, hybridToggle })
</script>

<template>
    <div class="message-input" ref="rootRef" :class="{ collapsed, 'message-input--has-panel-above': hasPanelAbove }">
        <!-- Collapsed bar: single line shown in place of the whole composer.
             Clickable anywhere to restore; the explicit button is the visual cue.
             Keeps the .message-input-collapsed-bar class so the collapsed-state
             "hide every other child" rule still excludes it. -->
        <CollapsedBar
            v-if="collapsed"
            class="message-input-collapsed-bar"
            :icon="collapsedIcon"
            :label="collapsedLabel"
            expand-tooltip="Expand the message input"
            :sidebar-toggle-clearance="true"
            @expand="requestExpandAndFocus"
        >
            <!-- Hybrid icon kept reachable while the composer is collapsed —
                 only when the embedded terminal block isn't already visible
                 above (open or its callout). Mirrors the expanded toolbar icon. -->
            <template v-if="isHybridAvailable && !terminalVisible" #trailing>
                <wa-button
                    :id="hybridButtonCollapsedId"
                    appearance="plain"
                    variant="neutral"
                    size="small"
                    class="hybrid-toggle-button"
                    :class="{ 'is-committed': isHybridCommitted, 'is-pending': hybridPending, 'needs-attention': isHybridCommitted && terminalAttention }"
                    @click.stop="handleHybridClick"
                >
                    <wa-icon name="terminal" variant="classic"></wa-icon>
                </wa-button>
                <AppTooltip :for="hybridButtonCollapsedId">{{ hybridTooltipLabel }}</AppTooltip>
            </template>
        </CollapsedBar>

        <!-- Floating "collapse" button: only when the composer is tall enough to
             be worth collapsing, and not already collapsed. Absolutely positioned
             so it never reflows the toolbar. Framed and brand-tinted so it
             reads unambiguously as a button over the textarea. -->
        <wa-button
            v-if="isTall && !collapsed"
            variant="brand"
            appearance="outlined"
            size="small"
            class="collapse-toggle-btn floating-over-text"
            :id="collapseButtonId"
            @click="onCollapseRequest"
        >
            <wa-icon name="chevron-down" variant="classic"></wa-icon>
        </wa-button>
        <AppTooltip v-if="isTall && !collapsed" :for="collapseButtonId">Collapse the message input</AppTooltip>

        <div v-if="commentsWithContentCount > 0" class="code-comments-bar">
            <wa-button
                variant="brand"
                appearance="filled-outlined"
                size="small"
                @click="addAllCommentsToMessage"
            >
                {{ commentsWithContentCount === 1
                    ? 'Add comment to message'
                    : `Add all comments (${commentsWithContentCount}) to message`
                }}
            </wa-button>
            <wa-button
                variant="neutral"
                appearance="outlined"
                size="small"
                @click="clearAllSessionComments"
            >
                {{ commentsWithContentCount === 1 ? 'Clear comment' : 'Clear comments' }}
            </wa-button>
        </div>
        <wa-textarea
            ref="textareaRef"
            :id="textareaAnchorId"
            :value.prop="messageText"
            :placeholder="placeholderText"
            rows="3"
            resize="none"
            @input="onInput"
            @keydown="onKeydown"
            @paste="onPaste"
            @focus="adjustTextareaHeight"
        ></wa-textarea>

        <!-- Popups teleported out of the flex container -->
        <Teleport to="body">
            <!-- File picker popup triggered by @ -->
            <FilePickerPopup
                ref="filePickerRef"
                :session-id="sessionId"
                :project-id="projectId"
                :anchor-id="textareaAnchorId"
                @select="onFilePickerSelect"
                @close="onFilePickerClose"
                @filter-change="onFilePickerFilterChange"
            />

            <!-- Command picker popup, opened on a provider activation char (e.g. '/') -->
            <CommandPickerPopup
                ref="commandPickerRef"
                :project-id="projectId"
                :provider="session?.provider"
                :activation-char="activeCommandChar"
                :anchor-id="textareaAnchorId"
                @select="onCommandSelect"
                @close="onCommandPickerClose"
                @filter-change="onCommandPickerFilterChange"
            />

            <!-- Message history picker popup triggered by ! at start -->
            <MessageHistoryPickerPopup
                ref="historyPickerRef"
                :project-id="projectId"
                :session-id="sessionId"
                :anchor-id="textareaAnchorId"
                :synthetic-message-text="optimisticMessageText"
                @select="onHistoryMessageSelect"
                @close="onHistoryPickerClose"
                @filter-change="onHistoryPickerFilterChange"
            />
        </Teleport>

        <!-- Message snippets bar -->
        <MessageSnippetsBar
            :snippets="snippetsForProject"
            :show-history-button="!isDraft"
            :activation-chars="commandActivationChars"
            :can-open-command="messageText.length === 0"
            @snippet-press="handleSnippetPress"
            @snippet-long-press="handleSnippetLongPress"
            @snippet-disabled-press="handleSnippetDisabledPress"
            @manage-snippets="openMessageSnippetsDialog"
            @open-history="openHistoryFromButton"
            @open-command="openCommandFromButton"
            @open-at="openAtFromButton"
        />

        <!-- Message snippets dialog (teleported out of the flex container) -->
        <Teleport to="body">
            <MessageSnippetsDialog
                ref="messageSnippetsDialogRef"
                :current-project-id="snippetListProjectId"
            />
        </Teleport>

        <div class="message-input-toolbar">
            <!-- Attachments row: button on left, thumbnails on right -->
            <div class="message-input-attachments">
                <!-- Hidden file input -->
                <input
                    v-if="canAttachAnything"
                    ref="fileInputRef"
                    type="file"
                    multiple
                    :accept="acceptedMimeTypesString"
                    style="display: none;"
                    @change="onFileSelected"
                />

                <!-- Attach button — hidden entirely when the provider takes no attachments -->
                <template v-if="canAttachAnything">
                    <wa-button
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        @click="openFilePicker"
                        :id="attachButtonId"
                    >
                        <wa-icon name="paperclip"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="attachButtonId">{{ attachTooltipLabel }}</AppTooltip>
                </template>

                <!-- Attachment badge + popover -->
                <template v-if="attachmentCount > 0">
                    <button
                        :id="`attachments-popover-trigger-${sessionId}`"
                        class="attachments-badge-trigger"
                    >
                        <wa-badge variant="primary" pill>{{ attachmentCount }}</wa-badge>
                    </button>
                    <AppTooltip :for="`attachments-popover-trigger-${sessionId}`">{{ attachmentCount }} file{{ attachmentCount > 1 ? 's' : '' }} attached</AppTooltip>
                    <!-- Temporary tooltip shown when new files are attached -->
                    <!-- `force`: driven manually after an attachment, so it must
                         also show on touch devices, where AppTooltip otherwise
                         renders nothing. -->
                    <AppTooltip
                        force
                        :for="`attachments-popover-trigger-${sessionId}`"
                        trigger="manual"
                        placement="top"
                        :open="showAttachTooltip || undefined"
                    >{{ attachTooltipText }}</AppTooltip>
                    <wa-popover
                        v-popover-focus-fix
                        :for="`attachments-popover-trigger-${sessionId}`"
                        placement="top"
                        class="attachments-popover"
                    >
                        <MediaThumbnailGroup
                            :items="mediaItems"
                            removable
                            @remove="removeAttachmentByIndex"
                        />
                        <div class="popover-actions">
                            <wa-button
                                variant="danger"
                                appearance="outlined"
                                size="small"
                                @click="removeAllAttachments"
                            >
                                <wa-icon name="trash" slot="start"></wa-icon>
                                Remove all
                            </wa-button>
                        </div>
                    </wa-popover>
                </template>
            </div>

            <div class="message-input-actions">
                <!-- Settings trigger: button (with summary) + popover -->
                <wa-button
                    :id="settingsButtonId"
                    appearance="plain"
                    variant="neutral"
                    size="small"
                    class="settings-button"
                >
                    <ProviderIcon v-if="providerIcon" :provider="session?.provider" />
                    <wa-icon v-else name="gear"></wa-icon>
                    <AgentSettingsSummary :session="session" :settings="settings" />
                    <!-- Affordance hinting the button opens the settings popover
                         (upward). Kept visible in every mode, including the most
                         compact icon-only one, via its own always-on display rule. -->
                    <wa-icon slot="end" name="chevron-up" class="settings-chevron"></wa-icon>
                </wa-button>
                <AgentSettingsPopover
                    :for="settingsButtonId"
                    :session="session"
                    :settings="settings"
                    :is-draft="isDraft"
                    :message-text="messageText"
                    :button-label="buttonLabel"
                    :sending-locked="sendingLocked"
                />

                <!-- Hybrid CLI mode toggle (Claude Code, visible sessions only).
                     Always clickable: committed sessions open the info dialog so a
                     click is never a no-op the user reads as a bug. Green =
                     committed, orange = pending (draft choice or staged switch). -->
                <wa-button
                    v-if="isHybridAvailable"
                    :id="hybridButtonId"
                    appearance="plain"
                    variant="neutral"
                    size="small"
                    class="hybrid-toggle-button"
                    :class="{ 'is-committed': isHybridCommitted, 'is-pending': hybridPending, 'needs-attention': isHybridCommitted && terminalAttention }"
                    @click="handleHybridClick"
                >
                    <wa-icon name="terminal" variant="classic"></wa-icon>
                </wa-button>
                <AppTooltip v-if="isHybridAvailable" :for="hybridButtonId">{{ hybridTooltipLabel }}</AppTooltip>

                <!-- Cancel button for draft sessions -->
                <wa-button
                    v-if="isDraft"
                    variant="neutral"
                    appearance="outlined"
                    @click="handleCancel"
                    size="small"
                    class="cancel-button"
                >
                    <wa-icon name="xmark" variant="classic"></wa-icon>
                    <span>Cancel</span>
                </wa-button>
                <!-- Reset/Clear button for existing sessions: "Reset" when agent
                     settings changed (with or without message/attachments), "Clear"
                     when only the message (text and/or attachments) is set. Both clear
                     text + attachments and/or restore dropdowns. -->
                <wa-button
                    v-else-if="messageText.trim() || attachmentCount > 0 || hasDropdownsChanged"
                    variant="neutral"
                    appearance="outlined"
                    @click="handleReset"
                    size="small"
                    class="reset-button"
                >
                    <wa-icon name="xmark" variant="classic"></wa-icon>
                    <span>{{ hasDropdownsChanged ? 'Reset' : 'Clear' }}</span>
                </wa-button>
                <!-- Send / Update button: dynamically labeled based on state.
                     A pending request replaces it with the paused indicator. Session
                     preparation keeps it visible but disabled. -->
                <wa-button
                    v-if="!sendingLocked || sendingLockedPresentation === 'disabled'"
                    :id="sendingLocked ? sendingLockedId : undefined"
                    variant="brand"
                    :disabled="sendingLocked || isDisabled || (!messageText.trim() && !canSendAttachmentsOnly && !hasUnappliedChanges)"
                    @click="handleSend"
                    size="small"
                    class="send-button"
                >
                    <wa-icon :name="buttonIcon" variant="classic"></wa-icon>
                    <span>{{ buttonLabel }}</span>
                </wa-button>
                <!-- Sending paused: shown in place of Send while a pending request
                     occupies the footer. The composer stays fully usable for
                     preparing the next message; it sends once the request is answered. -->
                <div v-else class="sending-locked-indicator" :id="sendingLockedId">
                    <wa-icon name="lock" variant="classic"></wa-icon>
                    <span>Sending paused</span>
                </div>
                <AppTooltip v-if="sendingLocked" :for="sendingLockedId">{{ sendingLockedReason }}</AppTooltip>
            </div>
        </div>

        <!-- Hybrid CLI mode dialog — one dialog, four variants (see script).
             The explanation lives in a <wa-details>, always present; only the
             intro, the details' open state, the "don't show again" switch and the
             footer buttons vary. Switch variants are forced-choice (no Esc / X /
             backdrop) so the synced "seen" flag is only written on a real press. -->
        <wa-dialog
            ref="hybridDialogRef"
            class="hybrid-dialog"
            :class="{ 'forced-choice': hybridDialogForcedChoice }"
            :label="hybridDialogTitle"
            style="--width: min(40rem, calc(100vw - 2rem))"
            @wa-hide="onHybridDialogHide"
            @wa-after-hide="onHybridDialogAfterHide"
            @wa-after-show="onHybridDialogAfterShow"
        >
            <p class="hybrid-dialog-intro">{{ hybridDialogIntro }}</p>
            <HybridModeExplainer :open="hybridDialogDetailsOpen" />

            <wa-switch
                v-if="hybridDialogHasSwitch"
                slot="footer"
                class="hybrid-dialog-switch"
                :checked="hybridDontShowAgain"
                @change="hybridDontShowAgain = $event.target.checked"
            >Don't show this again</wa-switch>
            <wa-button
                slot="footer"
                variant="neutral"
                appearance="outlined"
                @click="cancelHybridDialog"
            >Cancel</wa-button>
            <wa-button
                ref="hybridConfirmBtnRef"
                slot="footer"
                variant="brand"
                class="hybrid-dialog-confirm"
                @click="confirmHybridDialog"
            >{{ hybridConfirmLabel }}</wa-button>
        </wa-dialog>
    </div>
</template>

<style scoped>
.message-input {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-s);
    padding-top: calc(var(--wa-focus-ring-width) + var(--wa-focus-ring-offset));
    background: var(--main-header-footer-bg-color);
    container: message-input / inline-size;
    /* Anchor for the floating collapse button. */
    position: relative;
}

/* Collapsed: hide everything except the single-line bar. Children are kept in
   the DOM (display:none, not v-if) so the draft text, attachments and changed
   settings all survive a collapse/restore round-trip. */
.message-input.collapsed {
    /* The collapsed bar owns all the padding; the container adds none — except a
       bottom inset matching the bar's relative `top` shift, so the shifted bar
       never overflows below the footer background. */
    padding: 0;
    /* Hairline to read as footer chrome, distinct from the conversation above. */
    border-top: var(--divider-size) solid var(--wa-color-surface-border);
}
.message-input.collapsed > :not(.message-input-collapsed-bar) {
    display: none;
}

/* When another panel sits above the composer in the footer — a pending request,
   the hybrid terminal block and/or the goal bar — a hairline separates the composer from it
   (each of those panels itself sits under its own wa-divider). Mirrors the
   collapsed-state border so the separator is present whether the composer is a
   bar or expanded. */
.message-input.message-input--has-panel-above {
    border-top: var(--divider-size) solid var(--wa-color-surface-border);
}
/* Breathing room below the separator when the composer is expanded under the
   panel. (Collapsed, the bar owns its own padding.) */
.message-input.message-input--has-panel-above:not(.collapsed) {
    padding-top: var(--wa-space-s);
}

.collapse-toggle-btn {
    position: absolute;
    top: 10px;
    right: 22px;
    z-index: 1;
}
/* The translucent tint that keeps the textarea's first line readable under the
   button comes from the shared .floating-over-text class
   (styles/transcript-tokens.css). */

.code-comments-bar {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
    align-items: center;
}

.message-input wa-textarea::part(textarea) {
    /* Limit height to 40% of visual viewport (accounts for mobile keyboard) */
    max-height: 40dvh;
    /* Allow scrolling when content exceeds max-height */
    overflow-y: auto;
}

.message-input-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
    @media (width < 640px) {
        padding-left: 2.75rem;
    }
}

/* When the sidebar is closed its reopen toggle overlaps the toolbar's bottom-left; clear it. The
   amount is --sidebar-toggle-clearance-x, defined on body.sidebar-closed (App.vue) and refined per
   dock context on .session-layout — so it's always set whenever this rule applies. */
body.sidebar-closed .message-input-toolbar {
    @media (width >= 640px) {
        padding-left: var(--sidebar-toggle-clearance-x);
    }
}

.message-input-attachments {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    min-width: 0;
    flex-shrink: 0;
    /* React to the composer's own width, not the viewport. */
    @container message-input (width < 40rem) {
        gap: var(--wa-space-xs);
    }
}

.settings-button {
    wa-icon {
        display: none;
    }
    /* Opens-upward affordance, kept visible in every summary reduction level. */
    .settings-chevron {
        display: inline-flex;
        flex-shrink: 0;
        font-size: var(--wa-font-size-xs);
        color: var(--wa-color-text-quiet);
    }
    min-width: 0;
    flex-shrink: 1;
    &::part(base) {
        padding-inline: 0;
    }
    /* Bound the summary to the button's allotted width so its own graduated
       flex-shrink can kick in: min-width:0 lets the label shrink below its
       content, overflow:hidden clips whatever still doesn't fit. Without this
       the label keeps the summary at its full content width and it never
       shrinks (see AgentSettingsSummaryView for the per-part shrink priorities). */
    &::part(label) {
        min-width: 0;
        overflow: hidden;
        font-weight: normal;
        font-size: var(--wa-font-size-s);
    }
}

.hybrid-toggle-button {
    flex-shrink: 0;
    /* Green = committed hybrid session (locked in); orange = pending (a draft's
       choice, or a staged switch on an SDK session — still reversible until the
       next Send/Apply). The button is never disabled now, so no opacity fix. */
    &.is-committed::part(base) {
        color: var(--wa-color-success-fill-loud);
    }
    &.is-pending::part(base) {
        color: var(--wa-color-warning-fill-loud);
    }
    /* Committed + the embedded terminal needs the user (and is closed): tint
       warning to match the callout above the composer — overrides the committed
       green (declared after it so it wins at equal specificity). */
    &.needs-attention::part(base) {
        color: var(--wa-color-warning-fill-loud);
    }
}

/* Hybrid dialog footer: the "don't show again" switch sits left of the buttons;
   the close (X) is hidden on forced-choice variants so a footer button must be
   pressed (we never persist the "seen" flag on an Esc/backdrop dismissal). */
.hybrid-dialog-intro {
    margin-block-start: 0;
}
.hybrid-dialog-switch {
    margin-inline-end: auto;
    align-self: center;
}
.hybrid-dialog.forced-choice::part(close-button) {
    display: none;
}
/* Show the focus ring on the dialog's primary (Start / Switch) button even when
   focus arrived programmatically (@wa-after-show) — default :focus-visible skips
   non-keyboard focus, hiding the indicator. :focus-within (not :focus) because
   wa-button delegates focus into its shadow DOM; the host keeps :focus-within
   accurate via activeElement. Mirrors the pending-request primary-target rule. */
.hybrid-dialog-confirm:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

.message-input-actions {
    display: flex;
    gap: var(--wa-space-s);
    flex-shrink: 1;
    flex-grow: 1;
    min-width: 0;
    align-items: center;
    justify-content: flex-end;

    .cancel-button, .reset-button, .send-button {
        flex-shrink: 0;
        wa-icon {
            display: none;
        }
        & > span {
            display: inline-block;
        }
    }
}

/* "Sending paused" indicator shown in place of the Send button while a pending
   request locks sending. Non-interactive; sized to sit comfortably in the
   actions row next to the (still-active) Reset button. */
.sending-locked-indicator {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding-inline: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    font-weight: 500;
    white-space: nowrap;

    wa-icon {
        font-size: var(--wa-font-size-s);
    }
}

/* On narrow widths, show only icons for action buttons */
@container message-input (width < 25rem) {
    .message-input-actions {
        gap: var(--wa-space-2xs);

        .cancel-button, .reset-button, .send-button {
            &::part(base) {
                padding-inline: var(--wa-space-s);
            }

            wa-icon {
                display: inline-flex;
            }

            & > span {
                display: none;
            }
        }
    }
}

.attachments-badge-trigger {
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    display: flex;
    align-items: center;
    box-shadow: none;
    background: var(--wa-color-brand);
    height: 1.5rem;
    min-width: 1.5rem;
    margin-bottom: 0;
}

.attachments-popover {
    --max-width: min(400px, 90vw);
    --arrow-size: 16px;
}

.popover-actions {
    display: flex;
    justify-content: center;
    margin-top: var(--wa-space-l);
}

</style>
