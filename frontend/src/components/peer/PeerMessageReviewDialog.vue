<script setup>
/**
 * PeerMessageReviewDialog — read a pending peer message and route it.
 *
 * The receiving-side human gate (design §6): the full message (markdown +
 * attachments) is read here, then delivered to an existing session, to a new
 * session in a picked project, or refused. The message itself is never
 * editable — an optional recipient note is injected alongside instead.
 *
 * Also the read-back surface for a resolved message (reopened from the inbox
 * history): an already-delivered one offers the delivery pickers again (wrong
 * target picked, draft cleared), a refused or outbound one is read-only.
 *
 * The store summary paints the reading shell immediately. REST then loads the
 * full text without attachment bytes; attachment blocks have a separate,
 * size-gated request so large peer content never arrives implicitly.
 */
import { ref, computed, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { usePeersStore } from '../../stores/peers'
import { useDataStore, ALL_PROJECTS_ID, sessionSortComparator } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { useWorkspacesStore } from '../../stores/workspaces'
import { getProviderHelpers, getProviderLabel, getProviderOptions } from '../../providers'
import { SESSION_TIME_FORMAT } from '../../constants'
import { formatDate } from '../../utils/date'
import { apiFetch } from '../../utils/api'
import { renderMarkdown } from '../../utils/markdown'
import { sdkBlockToMediaItem } from '../../utils/fileUtils'
import {
    addPeerAttachmentsToDraft,
    firstCompatiblePeerProvider,
    firstCompatiblePeerProviderForMetadata,
    formatPeerContentBytes,
    mergePeerAttachments,
    peerAttachmentBytes,
    peerAttachmentCompatibilityError,
    peerContentAllowsDelivery,
    peerDeliveryTargetState,
    shouldConfirmPeerAttachments,
    shouldConfirmPeerMarkdown,
} from '../../utils/peerMessageContent'
import { resolveDraftProvider } from '../../utils/projectAgentDefaults'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import { computeSidebarSessionBlocks } from '../../utils/sidebarSessions'
import {
    activePeerResolutionAction,
    answeredByLabel,
    chooseReplyTargetSource,
    deliveryPickerTransition,
    existingSessionActionLabel,
    isReplyTargetPickerEligible,
    peerDeliveryActionVisibility,
    recoverReplyTargetPagination,
    shouldShowReplyTargetPreparation,
    waitForNextPaint,
} from '../../utils/peerReplyTarget'
import { dateBucketSeparator } from '../../utils/datePresets'
import { matchQuery } from '../../utils/textFilter'
import { isWorkspaceProjectId, extractWorkspaceId } from '../../utils/workspaceIds'
import { ensureProjectTrust } from '../../composables/useTrustGate'
import { useProjectMark } from '../../composables/useProjectMark'
import MediaThumbnailGroup from '../media/MediaThumbnailGroup.vue'
import ProjectBadge from '../project/ProjectBadge.vue'
import ProjectMark from '../project/ProjectMark.vue'
import ProjectSelectOptions from '../project/ProjectSelectOptions.vue'
import SessionListItem from '../session/list/SessionListItem.vue'
import SidebarListSeparator from '../sidebar/SidebarListSeparator.vue'

const props = defineProps({
    open: Boolean,
    messageId: { type: [Number, String], default: null },
})
// `close` carries an optional reason: 'navigating' when the dialog closes
// because the user is being sent to the delivery target (see navigateToComposer).
const emit = defineEmits(['close'])

const peersStore = usePeersStore()
const dataStore = useDataStore()
const settingsStore = useSettingsStore()
const router = useRouter()
const route = useRoute()

// The provenance timestamp follows the global time-format setting, like the
// inbox rows and every other timestamp in the app.
const sessionTimeFormat = computed(() => settingsStore.getSessionTimeFormat)
const useRelativeTime = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ||
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_NARROW
)
const relativeTimeFormat = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow'
)

const dialogRef = ref(null)
const detail = ref(null)          // summary shell, then lightweight detail + approved attachments
const detailReady = ref(false)
const loadError = ref('')
const renderedText = ref('')      // renderMarkdown is async — never bind the promise
const markdownState = ref('loading')      // loading | confirm | rendering | declined | ready | error
const attachmentsState = ref('unknown')  // unknown | confirm | loading | declined | ready | error
const loadedAttachments = ref({ images: [], documents: [] })
const note = ref('')
const actionError = ref('')
const busy = ref(false)
const confirmingRefuse = ref(false)

// Delivery pickers.
const mode = ref(null)            // null | 'existing' | 'new'
const pickedProjectId = ref('')   // 'new' mode: wa-select value
const sessionFilter = ref('')
const existingPickerMounted = ref(false)
const existingPickerPreparing = ref(false)
// 'existing' mode: the scope the session list is built from — the sidebar
// frame by default, narrowable to one project (or the frame's workspace).
const scopeId = ref(ALL_PROJECTS_ID)
// 'existing' mode: a click only SELECTS (highlight); the explicit Deliver
// button sends — no accidental one-click delivery.
const selectedSessionId = ref(null)
const markingDone = ref(false)
const activeResolutionAction = computed(() =>
    activePeerResolutionAction(busy.value, confirmingRefuse.value, mode.value, markingDone.value),
)
const NO_COMPATIBLE_PROVIDER_ERROR = 'No active provider can receive all attachments in this message. '
    + 'Activate a compatible provider to continue.'

// Ordinary request-lifetime state. The boolean carries no target identity or
// reason. The generation invalidates every result from a closed or reused
// dialog before that result can write state.
const targetHydrationSettled = ref(false)
let openGeneration = 0
let existingPickerMountPromise = null

const peerName = computed(() => peersStore.peerLabel(detail.value?.peer_id))
const origin = computed(() => detail.value?.origin || {})
const isInbound = computed(() => detail.value?.direction === 'in')
const isPending = computed(() => isInbound.value && detail.value?.status === 'pending')
// Redelivery (design decision, 2026-08-10): the owner routed the message into
// the wrong session, or cleared the prefilled draft. A delivered message stays
// re-routable — the peer already got its "delivered" answer, so nothing changes
// for them. A REFUSED one never reopens: that answer stands.
// Every resolution is reversible (design of 2026-09-01): any inbound message
// can be (re)delivered, marked done or refused. "Redeliverable" keeps its
// narrow meaning — a DELIVERED row being retargeted — for the wording and
// the backend's explicit `redeliver` opt-in.
const isRedeliverable = computed(() => isInbound.value && detail.value?.status === 'delivered')
const isResolved = computed(() => isInbound.value && !!detail.value && detail.value.status !== 'pending')
const canDeliver = computed(() => isInbound.value && !!detail.value)
const contentAllowsDelivery = computed(() => peerContentAllowsDelivery(
    detailReady.value,
    markdownState.value,
    attachmentsState.value,
))
const textSizeLabel = computed(() => formatPeerContentBytes(detail.value?.text_bytes || 0))
const attachmentCount = computed(() => detail.value?.attachments_meta?.length || 0)
const attachmentByteCount = computed(() => peerAttachmentBytes(detail.value?.attachments_meta))
const attachmentSizeLabel = computed(() => formatPeerContentBytes(attachmentByteCount.value))
const attachmentPrompt = computed(() => attachmentCount.value === 1
    ? `This attachment is large (${attachmentSizeLabel.value}). Load it?`
    : `These ${attachmentCount.value} attachments total ${attachmentSizeLabel.value}. Load them?`
)
// Attachment bytes are dropped 7 days after resolution — a late redelivery
// carries the text only.
const attachmentsLost = computed(() =>
    isResolved.value && detail.value?.purged && (detail.value?.attachments_meta?.length || 0) > 0
)
// Header and routing read exactly like an inbox row (PeerInboxRow.vue): same
// arrow, same colours, same labelled "<verb> session “…” in <project>" line.
// The two surfaces show the same facts; they must not describe them twice, in
// two different vocabularies.
const headIcon = computed(() => {
    if (isInbound.value && isPending.value) return 'envelope'
    return isInbound.value ? 'arrow-down' : 'arrow-up'
})
const directionLabel = computed(() =>
    isInbound.value ? `Received from ${peerName.value}` : `Sent to ${peerName.value}`
)
const statusVariant = computed(() => {
    if (detail.value?.status === 'delivered' || detail.value?.status === 'done') return 'success'
    if (detail.value?.status === 'pending') return 'neutral'
    return 'danger'
})
const sentAt = computed(() => {
    const iso = origin.value.sent_at
    return iso ? new Date(iso) : null
})
/** The local end of the exchange: where an inbound message landed, where an
 *  outbound one left from. Nothing of the peer's own context is shown — it
 *  never crosses the wire. */
const localRoute = computed(() => {
    // Title and project ride with the message, read live from the session row
    // server-side — never resolved against the front's store, and never an id.
    // A session that no longer exists (FK nulled) shows no line at all.
    const local = isInbound.value ? detail.value?.delivered_to_session : detail.value?.origin_session
    if (!local) return null
    return {
        label: isInbound.value ? 'Delivered to session' : 'Sent from session',
        title: local.title || 'Untitled session',
        projectId: local.project_id || null,
        sessionId: local.id,
    }
})

/** Who answered this message, from its replies' authorship — never from its
 *  status (see `answeredByLabel`). */
const answeredLine = computed(() => answeredByLabel(
    detail.value?.direction, detail.value?.latest_reply_author, peerName.value,
))

const replyRoute = computed(() => {
    const reply = detail.value?.reply_to_ref
    if (!reply?.title) return null
    return {
        label: reply.direction === 'out' ? 'In reply to your' : 'In reply to their',
        title: reply.title,
        // The answered message is a local row: the dialog can show it. Absent
        // on rows serialized before the id joined the ref.
        messageId: reply.id ?? null,
    }
})

/** Show the message this one answers, in place, without leaving the dialog.
 *  `keepChain`: an in-dialog jump must not lose how the current message was
 *  reached, so closing still walks back to the inbox when it came from there. */
function openRepliedMessage() {
    if (replyRoute.value?.messageId == null) return
    window.dispatchEvent(new CustomEvent('twicc:open-peer-inbox', {
        detail: { messageId: replyRoute.value.messageId, keepChain: true },
    }))
}

/** Authorship line, only when the message was written directly by a human
 *  (`origin.author`, sender-declared — absent means agent, the historical
 *  meaning). An outbound direct message has no origin session, so this line
 *  is its whole provenance. */
const authorLine = computed(() => {
    if (origin.value.author !== 'human') return null
    return isInbound.value
        ? `Written directly by ${peerName.value}'s user`
        : 'Written directly by you'
})

/** Reply — the direct human answer to a received message. It opens the
 *  compose dialog threaded on this message and resolves NOTHING: the delivery
 *  decision (deliver / refuse) stays whole. Only for inbound messages whose
 *  peer can still be written to. */
const canReply = computed(() =>
    isInbound.value
    && !!detail.value?.message_id
    && peersStore.getPeerById(detail.value?.peer_id)?.state === 'active'
)

function openReplyComposer() {
    const current = detail.value
    if (!current) return
    // Replaced, not dismissed: neutralize wa-dialog's focus restoration, which
    // would land after the composer focused its first field and steal it
    // (same internal as CommandPalette.vue).
    if (dialogRef.value) dialogRef.value.originalTrigger = null
    // 'compose': the composer replaces this dialog and reopens it on close
    // (App.vue records the return BEFORE the event below is dispatched).
    emit('close', 'compose')
    window.dispatchEvent(new CustomEvent('twicc:open-peer-compose', {
        detail: {
            peerId: current.peer_id,
            replyTo: current.message_id,
            replyToTitle: current.title || '',
            replyPending: isPending.value,
            returnTo: 'review',
        },
    }))
}

/** Open the session this message belongs to, exactly like clicking it in the
 *  sidebar: `sessionRouteLocation` keeps the current frame — the project
 *  filter and the active workspace — and changes only the session. */
function openLocalSession() {
    if (!localRoute.value?.sessionId) return
    const target = { id: localRoute.value.sessionId, project_id: localRoute.value.projectId }
    // 'navigating': the user leaves for the session, so the inbox must not
    // reopen on top of it.
    emit('close', 'navigating')
    router.push(sessionRouteLocation(target, route))
}

const mediaItems = computed(() => {
    if (attachmentsState.value !== 'ready') return []
    const payload = detail.value?.payload
    if (!payload) return []
    return [...(payload.images || []), ...(payload.documents || [])]
        .map(sdkBlockToMediaItem)
        .filter(Boolean)
})

const workspacesStore = useWorkspacesStore()

// Project pickers (both modes): the same wa-select + ProjectSelectOptions the
// other new-session flows use (badges, named/tree split, workspace priority),
// non-stale and non-archived. `include-worktrees` lists each repository's
// worktrees under it, like the sidebar's "New session" picker — a worktree is
// a delivery target of its own.
const selectableProjects = computed(() =>
    dataStore.getListableProjects.filter(p => !p.archived && !p.stale)
)

/** Is `projectId` offered by the scope select — a listed repository, or one of
 *  their listed worktrees? */
function isSelectableProject(projectId) {
    const project = dataStore.getProject(projectId)
    if (!project || project.archived || project.stale) return false
    if (!project.worktree_of) return selectableProjects.value.some(p => p.id === projectId)
    return selectableProjects.value.some(p => p.id === project.worktree_of)
}

// Current sidebar frame (the dialog is global — derive it from the route,
// exactly like SessionList does).
const effectiveProjectId = computed(() => route.params.projectId || ALL_PROJECTS_ID)
const activeWorkspaceId = computed(() => {
    if (isWorkspaceProjectId(effectiveProjectId.value)) return extractWorkspaceId(effectiveProjectId.value)
    return route.query.workspace || null
})
const activeWorkspace = computed(() =>
    activeWorkspaceId.value ? workspacesStore.getWorkspaceById(activeWorkspaceId.value) : null
)

/** The scope the session picker opens on: the sidebar frame — the project (or
 *  workspace) the user is already looking at is where a message most often
 *  goes. Anything the select does not offer falls back to all projects. */
function defaultScopeId() {
    const frameId = effectiveProjectId.value
    if (isWorkspaceProjectId(frameId)) return activeWorkspace.value ? frameId : ALL_PROJECTS_ID
    return isSelectableProject(frameId) ? frameId : ALL_PROJECTS_ID
}

// Icon + dot of the picked scope, for the select's own button (a wa-select
// shows the option's label as plain text, never its rendered content).
const { iconUrl: scopeIconUrl, dotColor: scopeDotColor } = useProjectMark(scopeId)

// `computeSidebarSessionBlocks` already applies these project exclusions to
// normal rows. The same set lets a hydrated page-omitted row use the exact
// non-pagination rule instead of an eligibility override.
const archivedProjectIds = computed(() => new Set(
    dataStore.getProjects.filter(project => project.archived).map(project => project.id),
))

/** Whether a hydrated row belongs to the supplied picker scope before any
 *  pagination bound. This checks scope only; eligibility stays in the shared
 *  pure predicate. */
function sessionBelongsToScope(session, projectScopeId) {
    if (!session) return false
    if (projectScopeId === ALL_PROJECTS_ID) return true
    if (isWorkspaceProjectId(projectScopeId)) {
        const workspaceId = extractWorkspaceId(projectScopeId)
        return workspacesStore.getVisibleProjectIds(workspaceId).includes(session.project_id)
    }
    return dataStore.getProjectScopeIds(projectScopeId).includes(session.project_id)
}

/** Build the existing-session rows from explicit inputs. Initialization uses
 *  the target's project and an empty filter without mutating live picker state. */
function buildSessionRows(projectScopeId, textFilter, paginationTarget = null) {
    const blocks = computeSidebarSessionBlocks({
        data: dataStore,
        workspaces: workspacesStore,
        effectiveProjectId: projectScopeId,
        activeWorkspaceId: activeWorkspaceId.value,
        sessionId: null,
        showArchived: false,
        showArchivedProjects: false,
        showActiveAcrossFilters: false,
    })
    const processStates = dataStore.processStates
    const compareSessions = sessionSortComparator(processStates)
    const normalCandidates = blocks.natural.filter(session =>
        isReplyTargetPickerEligible(session, archivedProjectIds.value),
    )
    const recoveryTarget = sessionBelongsToScope(paginationTarget, projectScopeId)
        ? paginationTarget
        : null
    const candidates = recoveryTarget
        ? recoverReplyTargetPagination(
            normalCandidates,
            recoveryTarget,
            archivedProjectIds.value,
            compareSessions,
        )
        : normalCandidates
    const nowMs = Date.now()
    const entries = candidates.map((session) => {
        if (session.pinned) {
            return { session, sectionKey: 'n-pinned', separator: { label: 'Pinned' } }
        }
        if (processStates[session.id] != null) {
            return { session, sectionKey: 'n-active', separator: { label: 'Active' } }
        }
        const bucket = dateBucketSeparator(session.mtime, nowMs)
        return { session, sectionKey: `n-${bucket.key}`, separator: bucket.entry }
    })
    // Same matching as the sidebar's filter: fuzzy by default, exact
    // substring when the query is wrapped/prefixed with a quote.
    const query = textFilter.trim()
    const visible = query
        ? entries.filter(e => matchQuery(query, e.session.title || e.session.id))
        : entries
    // A separator lands on the first VISIBLE session of each section.
    let prevSection = null
    return visible.map((entry) => {
        const withSeparator = entry.sectionKey !== prevSection
        prevSection = entry.sectionKey
        return { ...entry, separator: withSeparator ? entry.separator : null }
    })
}

const replyTargetSession = computed(() =>
    dataStore.getSession(detail.value?.reply_target) || null,
)
const replyTargetPickerEligible = computed(() =>
    isReplyTargetPickerEligible(replyTargetSession.value, archivedProjectIds.value),
)
// A reply to a message the owner wrote directly (compose dialog): the parent
// never had a session, so there is nothing to propose — and nothing missing.
const replyToDirectParent = computed(() =>
    detail.value?.reply_to_ref?.direction === 'out'
    && detail.value?.reply_to_ref?.author === 'human',
)
const replyTargetSettled = computed(() =>
    isPending.value
    && !deliveryGloballyBlocked.value
    && targetHydrationSettled.value
    && detail.value?.reply_to !== '',
)
const showReplyTargetWarning = computed(() =>
    replyTargetSettled.value
    && !replyToDirectParent.value
    && !replyTargetPickerEligible.value,
)
const showDirectParentHint = computed(() =>
    replyTargetSettled.value && replyToDirectParent.value,
)
const showReplyTargetPreparation = computed(() =>
    !deliveryGloballyBlocked.value
    && (
        shouldShowReplyTargetPreparation(detail.value, targetHydrationSettled.value)
        || (existingPickerPreparing.value && mode.value === 'existing')
    ),
)
// 'Existing session' picker: the sidebar's natural block, with the same
// ordering, section labels and text matching. A hydrated target is inserted
// only when the current page bound is the reason the normal rows omitted it.
const sessionRows = computed(() => {
    if (!existingPickerMounted.value) return []
    return buildSessionRows(scopeId.value, sessionFilter.value, replyTargetSession.value)
})

const selectedSession = computed(() =>
    sessionRows.value.find(r => r.session.id === selectedSessionId.value)?.session || null
)
function deliveryTargetState(provider, missingTargetError = '') {
    const target = provider
        ? {
            capabilities: getProviderHelpers(provider)?.getAttachmentSupport(),
            providerLabel: getProviderLabel(provider),
        }
        : null
    return peerDeliveryTargetState(
        detail.value?.payload,
        target,
        contentAllowsDelivery.value,
        missingTargetError,
    )
}
const existingSessionDeliveryState = computed(() =>
    deliveryTargetState(selectedSession.value?.provider),
)
function activeProviderTargets(preferred = null) {
    return [
        preferred,
        ...getProviderOptions().map(option => option.value).filter(provider => provider !== preferred),
    ]
        .filter(provider => provider && dataStore.isProviderAvailable(provider))
        .map(provider => ({
            provider,
            capabilities: getProviderHelpers(provider)?.getAttachmentSupport(),
        }))
}
const compatibleActiveProvider = computed(() => firstCompatiblePeerProviderForMetadata(
    attachmentsLost.value ? [] : detail.value?.attachments_meta,
    activeProviderTargets(),
))
const deliveryGloballyBlocked = computed(() =>
    detailReady.value && !compatibleActiveProvider.value,
)
const deliveryActionVisibility = computed(() => peerDeliveryActionVisibility(
    deliveryGloballyBlocked.value,
    detail.value?.status,
))
function compatibleProviderForProject(projectId) {
    if (!projectId) return null
    const preferred = resolveDraftProvider(
        projectId,
        dataStore.projects,
        settingsStore.defaultProvider,
    )
    return firstCompatiblePeerProvider(detail.value?.payload, activeProviderTargets(preferred))
}
const pickedProjectProvider = computed(() => compatibleProviderForProject(pickedProjectId.value))
const newSessionDeliveryState = computed(() =>
    deliveryTargetState(
        pickedProjectProvider.value,
        pickedProjectId.value ? NO_COMPATIBLE_PROVIDER_ERROR : '',
    ),
)
const existingSessionActionText = computed(() => existingSessionActionLabel(
    !!selectedSession.value,
    activeResolutionAction.value === 'existing',
))

function isCurrentOpen(generation, messageId) {
    return generation === openGeneration
        && props.open
        && props.messageId === messageId
}

function summaryForMessage(messageId) {
    return peersStore.messages.find(message => String(message.id) === String(messageId)) || null
}

function summaryShell(summary) {
    if (!summary) return null
    return {
        ...summary,
        payload: { text: '', images: [], documents: [] },
    }
}

function initialAttachmentsState(message) {
    if (!message) return 'unknown'
    if (message.purged || !(message.attachments_meta?.length)) return 'ready'
    return shouldConfirmPeerAttachments(message.attachments_meta) ? 'confirm' : 'loading'
}

/** Mount the expensive session page once, after the preparation state paints.
 *  Later mode switches keep the same DOM and scroll position. */
async function ensureExistingPickerMounted(generation, messageId) {
    if (existingPickerMounted.value) return true
    if (existingPickerMountPromise) return existingPickerMountPromise

    existingPickerPreparing.value = true
    const mountPromise = (async () => {
        await waitForNextPaint()
        if (!isCurrentOpen(generation, messageId)) return false
        existingPickerMounted.value = true
        await nextTick()
        return isCurrentOpen(generation, messageId)
    })()
    existingPickerMountPromise = mountPromise
    try {
        return await mountPromise
    } finally {
        if (existingPickerMountPromise === mountPromise) existingPickerMountPromise = null
        if (isCurrentOpen(generation, messageId)) existingPickerPreparing.value = false
    }
}

async function renderDetailText(text, generation, messageId) {
    markdownState.value = 'rendering'
    await waitForNextPaint()
    if (!isCurrentOpen(generation, messageId)) return
    try {
        const rendered = await renderMarkdown(text)
        if (!isCurrentOpen(generation, messageId)) return
        renderedText.value = rendered
        markdownState.value = 'ready'
    } catch {
        if (!isCurrentOpen(generation, messageId)) return
        markdownState.value = 'error'
    }
}

function renderCurrentMessage() {
    return renderDetailText(
        detail.value?.payload?.text || '',
        openGeneration,
        props.messageId,
    )
}

function declineMarkdown() {
    markdownState.value = 'declined'
}

async function loadMessageAttachments(
    generation = openGeneration,
    messageId = props.messageId,
) {
    if (!attachmentCount.value || detail.value?.purged) {
        attachmentsState.value = 'ready'
        return
    }
    attachmentsState.value = 'loading'
    await waitForNextPaint()
    if (!isCurrentOpen(generation, messageId)) return
    try {
        const response = await apiFetch(`/api/peer-messages/${messageId}/attachments/`)
        if (!isCurrentOpen(generation, messageId)) return
        if (!response.ok) {
            attachmentsState.value = 'error'
            return
        }
        const attachments = await response.json()
        if (!isCurrentOpen(generation, messageId)) return
        loadedAttachments.value = attachments
        detail.value = mergePeerAttachments(detail.value, attachments)
        attachmentsState.value = 'ready'
    } catch {
        if (!isCurrentOpen(generation, messageId)) return
        attachmentsState.value = 'error'
    }
}

function declineAttachments() {
    attachmentsState.value = 'declined'
}

async function scrollSeededTarget(generation, messageId, targetId) {
    await nextTick()
    if (!isCurrentOpen(generation, messageId)) return
    if (mode.value !== 'existing' || selectedSessionId.value !== targetId) return
    const picker = dialogRef.value?.querySelector('.pr-picker')
    if (!picker) return
    const expectedId = `session-button-${targetId}`
    const row = [...picker.querySelectorAll('.session-item')]
        .find(candidate => candidate.id === expectedId)
    row?.scrollIntoView({ block: 'nearest' })
}

async function initializeReplyTarget(loadedDetail, generation, messageId) {
    if (!(loadedDetail.direction === 'in' && loadedDetail.status === 'pending')) {
        if (isCurrentOpen(generation, messageId)) targetHydrationSettled.value = true
        return
    }
    const targetId = loadedDetail.reply_target
    if (targetId == null) {
        if (isCurrentOpen(generation, messageId)) targetHydrationSettled.value = true
        return
    }
    if (deliveryGloballyBlocked.value) {
        targetHydrationSettled.value = true
        return
    }

    const current = dataStore.getSession(targetId)
    const normalRows = current
        ? buildSessionRows(current.project_id, '')
        : []
    const source = chooseReplyTargetSource(
        targetId,
        normalRows.map(row => row.session),
    )
    let target = null
    let candidateRows = normalRows
    if (source.kind === 'candidate') {
        target = source.session
    } else {
        try {
            target = await dataStore.loadSessionById(source.sessionId)
        } catch {
            target = null
        }
        if (!isCurrentOpen(generation, messageId)) return
        if (isReplyTargetPickerEligible(target, archivedProjectIds.value)) {
            candidateRows = buildSessionRows(target.project_id, '', target)
        } else {
            candidateRows = []
        }
    }

    if (!isCurrentOpen(generation, messageId)) return
    const targetIsCandidate = target
        && candidateRows.some(row => row.session.id === targetId)
    if (!targetIsCandidate) {
        targetHydrationSettled.value = true
        return
    }

    targetHydrationSettled.value = true
    scopeId.value = target.project_id
    selectedSessionId.value = targetId
    mode.value = 'existing'
    if (!await ensureExistingPickerMounted(generation, messageId)) return
    await scrollSeededTarget(generation, messageId, targetId)
}

watch(() => [props.open, props.messageId], async ([open, messageId]) => {
    const generation = ++openGeneration
    if (!open || messageId == null) return
    const summary = summaryForMessage(messageId)
    loadedAttachments.value = { images: [], documents: [] }
    detail.value = summaryShell(summary)
    detailReady.value = false
    loadError.value = ''
    renderedText.value = ''
    markdownState.value = 'loading'
    attachmentsState.value = initialAttachmentsState(summary)
    note.value = ''
    actionError.value = ''
    mode.value = null
    pickedProjectId.value = ''
    sessionFilter.value = ''
    scopeId.value = defaultScopeId()
    selectedSessionId.value = null
    existingPickerMounted.value = false
    existingPickerPreparing.value = false
    existingPickerMountPromise = null
    targetHydrationSettled.value = false
    confirmingRefuse.value = false

    let loadedDetail
    try {
        const response = await apiFetch(
            `/api/peer-messages/${messageId}/?include_attachments=0`,
        )
        if (!isCurrentOpen(generation, messageId)) return
        if (!response.ok) {
            loadError.value = 'Could not load the message.'
            return
        }
        loadedDetail = await response.json()
        if (!isCurrentOpen(generation, messageId)) return
        detail.value = mergePeerAttachments(loadedDetail, loadedAttachments.value)
        detailReady.value = true
        // Redelivery: bring back the note typed the first time (empty on a
        // never-delivered message).
        note.value = loadedDetail.recipient_note || ''
    } catch {
        if (!isCurrentOpen(generation, messageId)) return
        // fetch rejects on network failure — never leave the dialog blank.
        loadError.value = 'Could not load the message — is the server reachable?'
        return
    }

    if (attachmentsState.value === 'unknown') {
        attachmentsState.value = initialAttachmentsState(loadedDetail)
    }
    if (attachmentsState.value === 'loading') {
        void loadMessageAttachments(generation, messageId)
    }

    // Large peer text needs an owner decision before any Markdown work. Small
    // text paints its local progress state before the renderer can block.
    if (shouldConfirmPeerMarkdown(loadedDetail.text_bytes)) {
        markdownState.value = 'confirm'
    } else {
        await renderDetailText(
            loadedDetail.payload?.text || '', generation, messageId,
        )
    }
    if (!isCurrentOpen(generation, messageId)) return
    await initializeReplyTarget(loadedDetail, generation, messageId)
}, { immediate: true, flush: 'sync' })

/** Toggle a delivery mode. The existing picker mounts once per dialog and
 *  keeps its scope, filter, selection, rows, and scroll while hidden. */
async function setMode(next) {
    const transition = deliveryPickerTransition(
        mode.value,
        next,
        existingPickerMounted.value,
        deliveryGloballyBlocked.value,
    )
    if (transition.dismissRefusalConfirmation) confirmingRefuse.value = false
    mode.value = transition.mode
    if (transition.prepareExisting) {
        await ensureExistingPickerMounted(openGeneration, props.messageId)
    }
}

function errorText(payload) {
    const errors = payload?.errors
    if (Array.isArray(errors) && errors.length) {
        return errors.map(e => e.message || e.code).join(' — ')
    }
    return payload?.error || 'Request failed.'
}

const PEER_RESOLUTION_TIMEOUT_MS = 40_000
const PEER_RESOLUTION_TIMEOUT_MESSAGE = 'The request did not complete in time. Refresh before trying again.'

class PeerResolutionTimeoutError extends Error {}

async function requestPeerResolution(url, options = {}) {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), PEER_RESOLUTION_TIMEOUT_MS)
    try {
        const response = await apiFetch(url, { ...options, signal: controller.signal })
        let payload = null
        try {
            payload = await response.json()
        } catch (error) {
            if (controller.signal.aborted) throw error
        }
        return { response, payload }
    } catch (error) {
        if (controller.signal.aborted) throw new PeerResolutionTimeoutError()
        throw error
    } finally {
        clearTimeout(timeoutId)
    }
}

function setActionFailure(error) {
    actionError.value = error instanceof PeerResolutionTimeoutError
        ? PEER_RESOLUTION_TIMEOUT_MESSAGE
        : 'Network error — could not reach the server.'
}

/** Rebuild a File from an SDK attachment block so the normal draft-attachment
 *  pipeline (validation, resize, IndexedDB) processes it like a user upload. */
function blockToFile(block, index) {
    const source = block?.source || {}
    if (source.type === 'text' && typeof source.data === 'string') {
        return new File([source.data], `peer-attachment-${index + 1}.txt`, { type: 'text/plain' })
    }
    if (source.type === 'base64' && typeof source.data === 'string') {
        const mime = source.media_type || 'application/octet-stream'
        const bytes = Uint8Array.from(atob(source.data), c => c.charCodeAt(0))
        const ext = mime === 'application/pdf' ? 'pdf' : (mime.split('/')[1] || 'bin')
        return new File([bytes], block.title || `peer-attachment-${index + 1}.${ext}`, { type: mime })
    }
    return null
}

/** Ask the backend to resolve the message as delivered; returns the envelope
 *  text to prefill a composer with, or null on failure (actionError set). */
async function markDelivered(sessionId) {
    const { response, payload } = await requestPeerResolution(
        `/api/peer-messages/${props.messageId}/deliver/`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: sessionId || undefined,
                note: note.value,
                // Opt-in server-side: an already-delivered message is only
                // re-routed when the UI asks for it explicitly.
                redeliver: isRedeliverable.value || undefined,
            }),
        },
    )
    if (!response.ok) {
        actionError.value = errorText(payload)
        return null
    }
    detail.value = { ...detail.value, status: 'delivered', recipient_note: note.value }
    return payload.envelope
}

function targetAttachmentError(provider) {
    const capabilities = getProviderHelpers(provider)?.getAttachmentSupport()
    return peerAttachmentCompatibilityError(
        detail.value?.payload,
        capabilities,
        getProviderLabel(provider),
    )
}

/** Prefill a composer (existing session's draft, or a fresh draft session)
 *  with the envelope + the peer attachments. Nothing is
 *  sent — the user reviews and sends through the normal pipeline. */
async function prefillComposer(sessionId) {
    const attachmentError = await addPeerAttachmentsToDraft(
        detail.value?.payload,
        blockToFile,
        file => dataStore.addAttachment(sessionId, file),
    )
    if (attachmentError) {
        actionError.value = attachmentError
        return false
    }

    // Append — the target session may already carry a user-typed draft,
    // which must never be overwritten.
    const existing = dataStore.getDraftMessage(sessionId)?.message?.trim() || ''
    dataStore.setDraftMessage(sessionId, existing ? `${existing}\n\n${envelopeText}` : envelopeText)
    return true
}

function navigateToComposer(sessionId, projectId) {
    // 'navigating': the user leaves for the target session — the inbox must
    // NOT come back over the composer they are being sent to.
    emit('close', 'navigating')
    router.push(sessionRouteLocation({ id: sessionId, project_id: projectId }, route))
}

let envelopeText = null

async function deliverToSession(session) {
    actionError.value = ''
    confirmingRefuse.value = false
    const compatibilityError = targetAttachmentError(session.provider)
    if (compatibilityError) {
        actionError.value = compatibilityError
        return
    }
    busy.value = true
    let shouldNavigate = false
    try {
        envelopeText = await markDelivered(session.id)
        if (envelopeText == null) return
        if (!await prefillComposer(session.id)) return
        shouldNavigate = true
    } catch (error) {
        setActionFailure(error)
    } finally {
        busy.value = false
    }
    if (shouldNavigate) navigateToComposer(session.id, session.project_id)
}

async function deliverToNewSession(projectId) {
    actionError.value = ''
    const provider = compatibleProviderForProject(projectId)
    if (!provider) {
        actionError.value = NO_COMPATIBLE_PROVIDER_ERROR
        return
    }
    // Trust gate before mutation: if the user backs out, the message stays pending.
    const gate = await ensureProjectTrust(projectId)
    if (!gate) return
    confirmingRefuse.value = false
    busy.value = true
    let draftId = null
    try {
        envelopeText = await markDelivered(null)
        if (envelopeText == null) return
        draftId = dataStore.createDraftSession(projectId, gate.state, provider)
        if (!await prefillComposer(draftId)) {
            await dataStore.clearAttachmentsForSession(draftId).catch(() => {})
            dataStore.deleteDraftSession(draftId)
            draftId = null
            return
        }
        // The delivery was just recorded with NO target: the session does not
        // exist yet. Tie the message to the draft so the store can complete the
        // link once the provider creates the real session — that is what makes
        // the inbox row point at it later.
        dataStore.setDraftPeerMessage(draftId, props.messageId)
    } catch (error) {
        setActionFailure(error)
        draftId = null
    } finally {
        busy.value = false
    }
    if (draftId != null) navigateToComposer(draftId, projectId)
}

async function refuse() {
    actionError.value = ''
    busy.value = true
    let shouldClose = false
    try {
        const { response, payload } = await requestPeerResolution(
            `/api/peer-messages/${props.messageId}/refuse/`,
            { method: 'POST' },
        )
        if (!response.ok) {
            actionError.value = errorText(payload)
            return
        }
        shouldClose = true
    } catch (error) {
        setActionFailure(error)
    } finally {
        busy.value = false
        confirmingRefuse.value = false
    }
    if (shouldClose) emit('close')
}

/** Done — read and dealt with by the owner, no agent. One click, no
 *  confirmation and no note: unlike refusing, nothing is being turned down,
 *  and unlike delivering, nothing reaches an agent. Reversible anyway. */
async function markDone() {
    actionError.value = ''
    busy.value = true
    markingDone.value = true
    let shouldClose = false
    try {
        const { response, payload } = await requestPeerResolution(
            `/api/peer-messages/${props.messageId}/done/`,
            { method: 'POST' },
        )
        if (!response.ok) {
            actionError.value = errorText(payload)
            return
        }
        shouldClose = true
    } catch (error) {
        setActionFailure(error)
    } finally {
        busy.value = false
        markingDone.value = false
    }
    if (shouldClose) emit('close')
}

function onHide(event) {
    if (event.target !== dialogRef.value) return
    if (busy.value) {
        event.preventDefault()
        return
    }
    emit('close')
}
</script>

<template>
    <wa-dialog
        ref="dialogRef" :open="open" label="Peer message"
        style="--width: min(720px, calc(100vw - 2rem))"
        @wa-hide="onHide"
    >
        <wa-callout v-if="loadError" variant="danger" size="small">{{ loadError }}</wa-callout>

        <template v-if="detail">
            <!-- Header — the inbox row's header, verbatim: direction arrow,
                 peer, state, time. -->
            <div class="pr-head">
                <wa-icon
                    :name="headIcon" :label="directionLabel" :title="directionLabel"
                    class="pr-head__icon" :class="isInbound ? 'pr-head__icon--in' : 'pr-head__icon--out'"
                ></wa-icon>
                <span class="pr-head__peer">{{ peerName }}</span>
                <span class="pr-head__fill"></span>
                <wa-tag :variant="statusVariant" size="small">{{ detail.status }}</wa-tag>
                <span v-if="sentAt" class="pr-head__time">
                    <wa-relative-time
                        v-if="useRelativeTime"
                        :date.prop="sentAt" :format="relativeTimeFormat"
                        numeric="always" sync
                    ></wa-relative-time>
                    <template v-else>{{ formatDate(Math.floor(sentAt.getTime() / 1000), { smart: true }) }}</template>
                </span>
            </div>

            <!-- The sender-written subject, between who speaks (header) and
                 what they say (quote) — the inbox row's reading order. Absent
                 on rows stored before the title became required. -->
            <h3 v-if="detail.title" class="pr-title">{{ detail.title }}</h3>

            <!-- Message body (markdown), quoted like the inbox preview: these
                 are someone else's words, not the app's. -->
            <div class="pr-quote">
                <div
                    v-if="markdownState === 'ready'"
                    class="pr-body markdown-body"
                    v-html="renderedText"
                ></div>
                <div v-else class="pr-body pr-content-state">
                    <template v-if="markdownState === 'loading' || markdownState === 'rendering'">
                        <span class="pr-content-state__status" role="status" aria-live="polite">
                            <wa-spinner></wa-spinner>
                            {{ markdownState === 'rendering' ? 'Rendering message…' : 'Loading message…' }}
                        </span>
                    </template>
                    <template v-else-if="markdownState === 'confirm'">
                        <span>This message is large ({{ textSizeLabel }}). Render it?</span>
                        <span class="pr-content-state__actions">
                            <wa-button size="small" variant="brand" @click="renderCurrentMessage">
                                Render message
                            </wa-button>
                            <wa-button size="small" appearance="outlined" @click="declineMarkdown">
                                Do not render
                            </wa-button>
                        </span>
                    </template>
                    <template v-else-if="markdownState === 'declined'">
                        <span>Message not rendered.</span>
                        <wa-button size="small" variant="brand" @click="renderCurrentMessage">
                            Render message
                        </wa-button>
                    </template>
                    <template v-else>
                        <span>Could not render the message.</span>
                        <wa-button size="small" variant="brand" @click="renderCurrentMessage">
                            Try again
                        </wa-button>
                    </template>
                </div>
            </div>

            <!-- Attachments -->
            <template v-if="attachmentCount && !detail.purged">
                <MediaThumbnailGroup
                    v-if="attachmentsState === 'ready' && mediaItems.length"
                    :items="mediaItems"
                />
                <div v-else class="pr-attachments-state">
                    <template v-if="attachmentsState === 'loading' || attachmentsState === 'unknown'">
                        <span class="pr-content-state__status" role="status" aria-live="polite">
                            <wa-spinner></wa-spinner>
                            Loading attachments…
                        </span>
                    </template>
                    <template v-else-if="attachmentsState === 'confirm'">
                        <span>{{ attachmentPrompt }}</span>
                        <span class="pr-content-state__actions">
                            <wa-button size="small" variant="brand" @click="loadMessageAttachments()">
                                Load attachments
                            </wa-button>
                            <wa-button size="small" appearance="outlined" @click="declineAttachments">
                                Do not load
                            </wa-button>
                        </span>
                    </template>
                    <template v-else-if="attachmentsState === 'declined'">
                        <span>Attachments not loaded.</span>
                        <wa-button size="small" variant="brand" @click="loadMessageAttachments()">
                            Load attachments
                        </wa-button>
                    </template>
                    <template v-else-if="attachmentsState === 'error'">
                        <span>Could not load attachments.</span>
                        <wa-button size="small" variant="brand" @click="loadMessageAttachments()">
                            Try again
                        </wa-button>
                    </template>
                </div>
            </template>
            <p v-else-if="detail.purged && attachmentCount && !attachmentsLost" class="pr-purged">
                {{ detail.attachments_meta.length }} attachment(s) — bytes purged.
            </p>

            <!-- Which message this one answers, then where it went / came
                 from. Both use the inbox row's label-then-value vocabulary. -->
            <p v-if="authorLine" class="pr-route">
                <span class="pr-route__label">{{ authorLine }}</span>
            </p>
            <p v-if="answeredLine" class="pr-route">
                <span class="pr-route__label">{{ answeredLine }}</span>
            </p>
            <p v-if="replyRoute" class="pr-route">
                <span class="pr-route__label">{{ replyRoute.label }}</span>
                <!-- Clickable when the answered row is known: shows it here,
                     like the local-session link below. -->
                <button
                    v-if="replyRoute.messageId != null"
                    type="button" class="pr-route__title pr-route__title--link"
                    :title="`Show “${replyRoute.title}”`"
                    @click="openRepliedMessage"
                >“{{ replyRoute.title }}”</button>
                <span v-else class="pr-route__title" :title="replyRoute.title">“{{ replyRoute.title }}”</span>
            </p>
            <p v-if="localRoute" class="pr-route">
                <span class="pr-route__label">{{ localRoute.label }}</span>
                <!-- Clickable when the session is known: goes there like a
                     sidebar row, keeping the current project/workspace frame. -->
                <button
                    v-if="localRoute.sessionId"
                    type="button" class="pr-route__title pr-route__title--link"
                    :title="`Open “${localRoute.title}”`"
                    @click="openLocalSession"
                >“{{ localRoute.title }}”</button>
                <span v-else class="pr-route__title" :title="localRoute.title">“{{ localRoute.title }}”</span>
                <template v-if="localRoute.projectId">
                    <span class="pr-route__label">in</span>
                    <ProjectBadge :project-id="localRoute.projectId" class="pr-route__project" />
                </template>
            </p>
            <wa-callout v-if="attachmentsLost" variant="warning" size="small">
                Its {{ detail.attachments_meta.length }} attachment(s) were purged — a new delivery
                carries the text only.
            </wa-callout>

            <!-- Actions -->
            <template v-if="canDeliver">
                <wa-callout
                    v-if="deliveryGloballyBlocked"
                    variant="warning" size="small"
                >
                    {{ NO_COMPATIBLE_PROVIDER_ERROR }}
                </wa-callout>
                <template v-else>
                    <wa-callout v-if="showReplyTargetWarning" variant="warning" size="small">
                        This message is part of a thread, but its session is not available for selection.
                        Choose another session, or deliver to a new one.
                    </wa-callout>
                    <p v-else-if="showDirectParentHint" class="pr-explainer">
                        In reply to a message you wrote directly — no session to propose.
                        Choose any session, or deliver to a new one.
                    </p>
                    <div class="pr-note">
                        <label class="pr-note__label" for="pr-note-input">Add a message for your agent (optional)</label>
                        <wa-textarea
                            id="pr-note-input"
                            size="small" rows="2"
                            placeholder="Delivered next to the peer's message, attributed to you"
                            :value="note"
                            @input="note = $event.target.value"
                        ></wa-textarea>
                    </div>

                    <p class="pr-explainer">
                        <template v-if="isRedeliverable">This message was already delivered; delivering it
                        again is allowed. </template><template v-else-if="isResolved">This message was
                        already {{ detail.status }}; any decision can be changed later — the sender
                        keeps the first one it was told. </template>Delivering does not send anything:
                        the message is placed in the chosen session's input (an existing one, or a new
                        draft) — you review it, adjust it if needed, and send it yourself.
                    </p>
                </template>

                <!-- The whole point of the dialog: filled brand, never a quiet
                     outline. The picked one stays filled, the other steps back
                     to an outline so the choice is readable. Done and Refuse
                     are the two answers that reach no agent; each hides in its
                     own state only — every resolution is reversible. -->
                <div
                    class="pr-actions"
                    :class="{ 'pr-actions--no-delivery': !deliveryActionVisibility.delivery }"
                >
                    <wa-button
                        v-if="deliveryActionVisibility.delivery"
                        variant="brand" :appearance="mode === 'new' ? 'outlined' : 'accent'"
                        :disabled="busy || !contentAllowsDelivery"
                        @click="setMode('existing')"
                    >
                        <wa-icon name="comments" slot="start"></wa-icon>
                        Deliver to existing session
                    </wa-button>
                    <wa-button
                        v-if="deliveryActionVisibility.delivery"
                        variant="brand" :appearance="mode === 'existing' ? 'outlined' : 'accent'"
                        :disabled="busy || !contentAllowsDelivery"
                        @click="setMode('new')"
                    >
                        <wa-icon name="plus" slot="start"></wa-icon>
                        Deliver to new session
                    </wa-button>
                    <!-- Not gated on attachment loading: that gate exists for
                         bytes reaching an agent, and Done reaches none. -->
                    <wa-button
                        v-if="deliveryActionVisibility.done"
                        variant="neutral" appearance="outlined"
                        :disabled="busy"
                        :aria-busy="activeResolutionAction === 'done' ? 'true' : 'false'"
                        @click="markDone"
                    >
                        <wa-spinner v-if="activeResolutionAction === 'done'" slot="start"></wa-spinner>
                        <wa-icon v-else name="check" slot="start"></wa-icon>
                        {{ activeResolutionAction === 'done' ? 'Marking…' : 'Done' }}
                    </wa-button>
                    <wa-button
                        v-if="deliveryActionVisibility.refusal"
                        size="small" variant="danger" appearance="outlined"
                        class="pr-actions__refuse"
                        :disabled="busy"
                        @click="confirmingRefuse = true"
                    >Refuse</wa-button>
                </div>

                <div
                    v-if="showReplyTargetPreparation"
                    class="pr-preparing" role="status" aria-live="polite"
                >
                    <wa-spinner></wa-spinner>
                    <span>Preparing session selection…</span>
                </div>

                <wa-callout v-if="confirmingRefuse" variant="warning" size="small">
                    <div class="pr-confirm-body">
                        <span>Refuse this message? The sender will see it as refused.</span>
                        <!-- A refusal carries no words. Rather than a reason
                             field nobody can answer, point at the reply that
                             already does it — and resolves the message in the
                             same gesture (its "Refuse it" choice). -->
                        <span v-if="canReply" class="pr-confirm__hint">
                            To explain why, use <strong>Reply manually</strong> at the bottom
                            of this dialog and pick <strong>Refuse it</strong> there.
                        </span>
                        <span class="pr-confirm__actions">
                            <wa-button
                                size="small" variant="danger" :disabled="busy"
                                :aria-busy="activeResolutionAction === 'refuse' ? 'true' : 'false'"
                                @click="refuse"
                            >
                                <wa-spinner v-if="activeResolutionAction === 'refuse'" slot="start"></wa-spinner>
                                {{ activeResolutionAction === 'refuse' ? 'Refusing…' : 'Refuse' }}
                            </wa-button>
                            <wa-button
                                size="small" appearance="outlined" :disabled="busy"
                                @click="confirmingRefuse = false"
                            >Keep</wa-button>
                        </span>
                    </div>
                </wa-callout>

                <!-- 'New session' mode: the same project selector as every
                     new-session flow (badges, named/tree split, ws priority). -->
                <template v-if="deliveryActionVisibility.delivery && mode === 'new'">
                    <div class="pr-new-session">
                        <wa-select
                            v-model="pickedProjectId"
                            size="small" placeholder="Pick a project…"
                        >
                            <ProjectSelectOptions
                                :projects="selectableProjects"
                                :priority-project-ids="activeWorkspace?.projectIds || null"
                                :priority-label="activeWorkspace ? `${activeWorkspace.name} projects` : null"
                                :priority-color="activeWorkspace?.color || null"
                                show-process-indicator
                                include-worktrees
                            />
                        </wa-select>
                        <wa-button
                            size="small" variant="brand"
                            :disabled="busy || newSessionDeliveryState.disabled"
                            :aria-busy="activeResolutionAction === 'new' ? 'true' : 'false'"
                            @click="deliverToNewSession(pickedProjectId)"
                        >
                            <wa-spinner v-if="activeResolutionAction === 'new'" slot="start"></wa-spinner>
                            <wa-icon v-else name="pen-to-square" slot="start"></wa-icon>
                            {{ activeResolutionAction === 'new' ? 'Delivering…' : 'Create draft session' }}
                        </wa-button>
                    </div>
                    <wa-callout
                        v-if="newSessionDeliveryState.error"
                        variant="warning" size="small"
                    >{{ newSessionDeliveryState.error }}</wa-callout>
                </template>

                <!-- 'Existing session' mode: the sidebar's session list (same
                     order and blocks, compact rendering), minus archived and
                     drafts. Click selects; the button delivers. -->
                <div
                    v-if="deliveryActionVisibility.delivery && existingPickerMounted"
                    v-show="mode === 'existing'"
                    class="pr-existing-session"
                >
                    <div class="pr-existing-action">
                        <wa-button
                            size="small" variant="brand"
                            :disabled="busy || existingSessionDeliveryState.disabled"
                            :aria-busy="activeResolutionAction === 'existing' ? 'true' : 'false'"
                            @click="deliverToSession(selectedSession)"
                        >
                            <wa-spinner v-if="activeResolutionAction === 'existing'" slot="start"></wa-spinner>
                            <wa-icon v-else name="pen-to-square" slot="start"></wa-icon>
                            {{ existingSessionActionText }}
                        </wa-button>
                        <wa-callout
                            v-if="existingSessionDeliveryState.error"
                            variant="warning" size="small"
                            class="pr-target-warning"
                        >{{ existingSessionDeliveryState.error }}</wa-callout>
                        <wa-callout
                            v-if="actionError"
                            variant="danger" size="small"
                            class="pr-action-error"
                        >{{ actionError }}</wa-callout>
                    </div>
                    <!-- Two filters, coarse then fine: the project (the same
                         selector as 'new session', plus the current sidebar
                         frame's scopes) narrows the list, the text input
                         searches inside it. -->
                    <div class="pr-picker-filters">
                        <wa-select v-model="scopeId" size="small" class="pr-picker-scope">
                            <ProjectMark
                                v-if="scopeId !== ALL_PROJECTS_ID && !isWorkspaceProjectId(scopeId)"
                                slot="start"
                                style="--project-mark-icon-size: var(--wa-space-m); --project-mark-size: 0.75em"
                                :icon-url="scopeIconUrl"
                                :color="scopeDotColor"
                            />
                            <wa-icon
                                v-else-if="isWorkspaceProjectId(scopeId)" slot="start" name="layer-group"
                                :style="activeWorkspace?.color ? { color: activeWorkspace.color } : null"
                            ></wa-icon>
                            <wa-option :value="ALL_PROJECTS_ID">All projects</wa-option>
                            <wa-option v-if="activeWorkspace" :value="`workspace:${activeWorkspace.id}`" :label="activeWorkspace.name">
                                <span class="pr-scope-workspace">
                                    <wa-icon name="layer-group" auto-width :style="activeWorkspace.color ? { color: activeWorkspace.color } : null"></wa-icon>
                                    {{ activeWorkspace.name }}
                                </span>
                            </wa-option>
                            <wa-divider></wa-divider>
                            <ProjectSelectOptions
                                :projects="selectableProjects"
                                :priority-project-ids="activeWorkspace?.projectIds || null"
                                :priority-label="activeWorkspace ? `${activeWorkspace.name} projects` : null"
                                :priority-color="activeWorkspace?.color || null"
                                show-process-indicator
                                include-worktrees
                            />
                        </wa-select>
                        <!-- `with-clear`: the same one-click reset as the
                             sidebar's filter, whose matching this reuses. -->
                        <wa-input
                            size="small" placeholder="Filter sessions…" class="pr-picker-search"
                            with-clear
                            :value="sessionFilter"
                            @input="sessionFilter = $event.target.value"
                        ></wa-input>
                    </div>
                    <!-- The REAL sidebar row component (compact mode): identical
                         icons, colors, heights and active-session highlight.
                         Its plain left click only emits `select` (no navigation);
                         the session-actions menu is hidden via CSS below. -->
                    <div class="pr-picker">
                        <template v-for="row in sessionRows" :key="row.session.id">
                            <SidebarListSeparator v-if="row.separator" v-bind="row.separator" />
                            <SessionListItem
                                :session="row.session"
                                :active="selectedSessionId === row.session.id"
                                compact-view
                                show-project-name
                                :show-title-tooltip="false"
                                @select="selectedSessionId = row.session.id"
                            />
                        </template>
                        <p v-if="!sessionRows.length" class="pr-empty">No matching session.</p>
                    </div>
                </div>

                <wa-callout
                    v-if="actionError && (mode !== 'existing' || !existingPickerMounted)"
                    variant="danger" size="small"
                    class="pr-action-error"
                >{{ actionError }}</wa-callout>
            </template>
        </template>

        <div v-else-if="!loadError" class="pr-dialog-loading" role="status" aria-live="polite">
            <wa-spinner></wa-spinner>
            Loading message…
        </div>

        <div slot="footer" class="pr-footer">
            <!-- Reply opens the direct composer, threaded on this message; it
                 never delivers or refuses — the trust-gate actions above stay
                 the only resolutions. -->
            <wa-button
                v-if="canReply"
                appearance="outlined" :disabled="busy"
                @click="openReplyComposer"
            >Reply manually</wa-button>
            <wa-button :disabled="busy" @click="emit('close')">Close</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
/* ── Header, quote and routing: the inbox row's vocabulary ─────────────── */
.pr-head {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-s);
    min-width: 0;
}
.pr-head__fill { flex: 1; }
/* Inbound wears the same brand colour as the incoming-message toast,
   outbound its own hue — identical to PeerInboxRow. */
.pr-head__icon { flex-shrink: 0; }
.pr-head__icon--in { color: var(--wa-color-brand-fill-loud, var(--wa-color-brand-60)); }
.pr-head__icon--out { color: var(--wa-color-success-fill-loud, var(--wa-color-success-60)); }
.pr-head__peer {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
}
.pr-head__time {
    color: var(--wa-color-text-quiet);
    font-size: 0.8rem;
    white-space: nowrap;
    flex-shrink: 0;
}

/* The subject: a heading of this dialog's content, not of the app chrome —
   sized between the header line and the body text. Wraps freely: the full
   title is the point of this surface (the inbox row ellipsizes it). */
.pr-title {
    margin: 0 0 var(--wa-space-s);
    font-size: var(--wa-font-size-l);
    line-height: var(--wa-line-height-condensed);
    overflow-wrap: anywhere;
}

/* The quote recipe of the markdown renderer (MarkdownContent.vue): quiet
   brand fill, left accent bar, square on the bar's side. The tint lives on
   the wrapper because `.markdown-body` paints its own background. */
.pr-quote {
    margin-bottom: var(--wa-space-s);
    border-radius: var(--wa-border-radius-m);
    border-start-start-radius: 0;
    border-end-start-radius: 0;
    border-inline-start: 2px solid var(--wa-color-brand-fill-loud);
    background: var(--wa-color-brand-fill-quiet);
}
.wa-dark .pr-quote { background: var(--wa-color-brand-fill-normal); }
.pr-body {
    padding: var(--wa-space-s) var(--wa-space-m);
    max-height: 40vh;
    overflow: auto;
    background: transparent;
    color: var(--wa-color-text-normal);
}
.pr-content-state,
.pr-attachments-state {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
}
.pr-content-state { min-height: 3rem; }
.pr-content-state__status,
.pr-content-state__actions {
    display: inline-flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
}
.pr-content-state__status wa-spinner { font-size: 1rem; }
.pr-dialog-loading {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-height: 4rem;
    color: var(--wa-color-text-quiet);
}
.pr-dialog-loading wa-spinner { font-size: 1rem; }
.pr-attachments-state {
    padding: var(--wa-space-s) var(--wa-space-m);
    margin-bottom: var(--wa-space-s);
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
    background: var(--wa-color-surface-lowered);
}
/* First and last blocks of the markdown must not push the tint open. */
.pr-body :deep(> :first-child) { margin-top: 0; }
.pr-body :deep(> :last-child) { margin-bottom: 0; }

.pr-route {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs);
    margin: 0 0 var(--wa-space-2xs);
    font-size: 0.85rem;
    min-width: 0;
}
.pr-route__label { color: var(--wa-color-text-quiet); flex-shrink: 0; }
.pr-route__title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 6ch;
}
/* A native <button> as an inline link: WA's native.css gives buttons a
   form-control height and centring, all of which must be reset here. */
.pr-route__title--link {
    height: auto;
    min-height: 0;
    padding: 0;
    border: none;
    background: none;
    font: inherit;
    color: var(--wa-color-brand-on-quiet);
    text-align: left;
    cursor: pointer;
}
.pr-route__title--link:hover { text-decoration: underline; }
.pr-route__project { max-width: 20ch; }

.pr-purged { color: var(--wa-color-text-quiet); font-size: 0.85rem; }
/* Three kinds of text share this dialog and must not read alike: the routing
   line is metadata (quiet, small), this is a FORM LABEL (normal colour, at
   text size), and the explainer below is a side note (quiet, italic). */
.pr-note { display: flex; flex-direction: column; gap: var(--wa-space-2xs); margin: var(--wa-space-m) 0 var(--wa-space-s); }
.pr-note__label { font-weight: var(--wa-font-weight-semibold); color: var(--wa-color-text-normal); }
.pr-actions {
    display: flex;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
    align-items: center;
    margin-bottom: var(--wa-space-s);
}
/* Refusing is a rare, destructive answer: kept away from the two delivery
   buttons so it is never the one clicked by reflex. */
.pr-actions__refuse { margin-inline-start: auto; }
.pr-actions--no-delivery .pr-actions__refuse { margin-inline-start: 0; }
.pr-preparing {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
}
.pr-preparing wa-spinner { font-size: 1rem; }
.pr-existing-action {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-s);
}
.pr-existing-action .pr-action-error,
.pr-existing-action .pr-target-warning { align-self: stretch; }
.pr-picker-filters {
    display: flex;
    gap: var(--wa-space-xs);
    align-items: center;
}
/* The project scope stays secondary: the text search takes the free space. */
.pr-picker-scope { flex: 0 1 40%; min-width: 0; }
.pr-picker-search { flex: 1 1 auto; min-width: 0; }
.pr-scope-workspace {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}
.pr-picker {
    max-height: 30vh;
    overflow: auto;
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
    margin-top: var(--wa-space-xs);
    margin-bottom: var(--wa-space-s);
    /* SidebarListSeparator's threshold labels expand to their full wording
       ("Older than 7 days" vs "7 days +") via an anonymous container query;
       without a container ancestor the query never matches and the compact
       form shows. The picker is always wide enough for the full form. */
    container-type: inline-size;
}
/* The rows are real SessionListItems (visuals owned by the component). Only
   the session-actions "…" menu is out of place in a delivery picker. */
.pr-picker :deep(.session-menu) { display: none !important; }
.pr-empty { color: var(--wa-color-text-quiet); padding: var(--wa-space-s); margin: 0; }
.pr-new-session {
    display: flex;
    gap: var(--wa-space-xs);
    align-items: center;
    margin-bottom: var(--wa-space-2xs);
}
.pr-explainer {
    /* Stands in for a callout in the same slot (the direct-parent hint), so it
       carries the same separation. Adjacent margins collapse: where it already
       follows a block with its own bottom margin, nothing grows. */
    margin: var(--wa-space-s) 0;
    color: var(--wa-color-text-quiet);
    font-style: italic;
    font-size: var(--wa-font-size-s);
}
.pr-new-session wa-select { flex: 1; min-width: 0; }
.pr-confirm-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}
.pr-inline-callout {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    flex-wrap: wrap;
}
.pr-confirm__actions {
    display: flex;
    gap: var(--wa-space-s);
}
/* Secondary to the question above it: the way out, not the decision. */
.pr-confirm__hint {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}
.pr-footer { display: flex; justify-content: flex-end; gap: var(--wa-space-s); width: 100%; }

/* Callouts sitting directly in the dialog body are block-flow siblings, and
   which one renders depends on state (load error, purged attachments, thread
   warning, refusal confirmation, delivery error…) — so the separation belongs
   to the callout, never to the block that happens to precede it. Adjacent
   margins collapse, so two stacked callouts still keep a single gap. Callouts
   nested in a flex row (.pr-existing-action) keep that row's gap instead. */
wa-dialog > wa-callout { margin-block: var(--wa-space-s); }
</style>
