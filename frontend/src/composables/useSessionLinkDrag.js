// Shared state for inserting session links into a composer: the chats that can
// receive one (registered while their composer is mounted) and the drag of a
// sidebar session in progress.

import { computed, reactive, shallowReactive } from 'vue'
import { useRoute } from 'vue-router'
import { useFramePoolStore } from '../stores/framePool.js'

// sessionId -> { el: () => HTMLElement | null, insert: (markdown, { input }) => Promise<boolean> }
// Shallow: entries stay raw objects, so an unregister can compare them by identity.
const composers = shallowReactive(new Map())

/**
 * Register the chat of `sessionId` as able to receive a session link. Returns
 * the unregister function; an older unregister never removes a newer entry.
 */
export function registerSessionLinkComposer(sessionId, entry) {
    composers.set(sessionId, entry)
    return () => {
        if (composers.get(sessionId) === entry) composers.delete(sessionId)
    }
}

export function getSessionLinkComposer(sessionId) {
    return (sessionId && composers.get(sessionId)) || null
}

/**
 * The session that receives a link: the session open in the main pane, when
 * its chat shows a composer. Null otherwise.
 */
export function useSessionLinkTarget(route = useRoute()) {
    const targetSessionId = computed(() => {
        const id = route.params.sessionId
        return id && composers.has(id) ? id : null
    })
    return { targetSessionId }
}

// The drag of a sidebar session in progress: `session` is `{ id, title, projectId,
// markdown }`, `x`/`y` the pointer position, `pointerType` the input, and
// `overSessionId` the chat a release would insert the link into.
export const sessionLinkDrag = reactive({ session: null, x: 0, y: 0, pointerType: null, overSessionId: null })

// A release counts as a drop only after this much travel since the drag started:
// in the narrow layout the finger is already over the chat when the drawer closes.
const MIN_DROP_TRAVEL = 10
// Elements of the chat that can receive a dropped link carry this attribute.
export const SESSION_LINK_DROP_ATTR = 'data-session-link-drop'

let active = null

function chatSessionIdAt(x, y) {
    const zone = document.elementFromPoint(x, y)?.closest?.(`[${SESSION_LINK_DROP_ATTR}]`)
    return zone?.getAttribute(SESSION_LINK_DROP_ATTR) || null
}

function updateOver() {
    const { session, x, y } = sessionLinkDrag
    const id = active.travel >= MIN_DROP_TRAVEL ? chatSessionIdAt(x, y) : null
    sessionLinkDrag.overSessionId = id && id !== session.id && composers.has(id) ? id : null
}

function onPointerMove(event) {
    if (event.pointerId !== active.pointerId) return
    event.preventDefault()
    active.travel += Math.hypot(event.clientX - sessionLinkDrag.x, event.clientY - sessionLinkDrag.y)
    sessionLinkDrag.x = event.clientX
    sessionLinkDrag.y = event.clientY
    updateOver()
}

async function onPointerUp(event) {
    if (event.pointerId !== active.pointerId) return
    const { session, overSessionId, pointerType } = sessionLinkDrag
    const entry = overSessionId && composers.get(overSessionId)
    finish()
    const inserted = entry ? await entry.insert(session.markdown, { input: pointerType === 'mouse' ? 'mouse' : 'touch' }) : false
    window.dispatchEvent(new CustomEvent('twicc:session-link-drag-end', { detail: { inserted } }))
}

function onPointerCancel(event) {
    if (event.pointerId === active.pointerId) cancelSessionLinkDrag()
}

function onKeydown(event) {
    if (event.key !== 'Escape') return
    event.preventDefault()
    cancelSessionLinkDrag()
}

// A drag that ends over another element of the page must not click it.
function onCapturedClick(event) {
    if (Date.now() >= suppressClickUntil) return
    event.preventDefault()
    event.stopPropagation()
}
let suppressClickUntil = 0
let clickGuardInstalled = false

function finish() {
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', onPointerUp)
    window.removeEventListener('pointercancel', onPointerCancel)
    window.removeEventListener('keydown', onKeydown)
    document.body.style.userSelect = active.previousUserSelect
    document.body.classList.remove('session-link-dragging')
    active.framePool.endDividerDrag()
    suppressClickUntil = Date.now() + 500
    active = null
    Object.assign(sessionLinkDrag, { session: null, pointerType: null, overSessionId: null })
}

/**
 * Start dragging a sidebar session (same model as the layout tab drag: pointer
 * events followed on the window). `session` is `{ id, title, projectId, markdown }`.
 * Emits `twicc:session-link-drag-start` and, when it ends, `twicc:session-link-drag-end`
 * with `{ inserted }`, so the mobile drawer can close and reopen.
 */
export function startSessionLinkDrag(session, { pointerId, pointerType, x, y }) {
    if (active) cancelSessionLinkDrag()
    if (!clickGuardInstalled) {
        window.addEventListener('click', onCapturedClick, true)
        clickGuardInstalled = true
    }
    // Like the layout tab drag: the pooled iframes (inline artifacts, previews) stop taking
    // pointer events, so the drag keeps receiving them and sees the chat under an iframe.
    const framePool = useFramePoolStore()
    framePool.beginDividerDrag()
    active = { pointerId, travel: 0, previousUserSelect: document.body.style.userSelect, framePool }
    Object.assign(sessionLinkDrag, { session, x, y, pointerType, overSessionId: null })
    document.body.style.userSelect = 'none'
    document.body.classList.add('session-link-dragging')
    window.addEventListener('pointermove', onPointerMove, { passive: false })
    window.addEventListener('pointerup', onPointerUp)
    window.addEventListener('pointercancel', onPointerCancel)
    window.addEventListener('keydown', onKeydown)
    window.dispatchEvent(new CustomEvent('twicc:session-link-drag-start'))
}

export function cancelSessionLinkDrag() {
    if (!active) return
    finish()
    window.dispatchEvent(new CustomEvent('twicc:session-link-drag-end', { detail: { inserted: false } }))
}

export function isSessionLinkDragActive() {
    return !!active
}
