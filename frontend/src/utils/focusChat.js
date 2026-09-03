/**
 * Focus helpers for the session chat surface (message input + pending request
 * form). Used by the global Alt+Shift+M shortcut, the per-session keyboard tab
 * navigation, the command palette "Focus Message Input" action — anything that
 * wants to land on "the primary thing the user can act on right now" in the
 * chat panel.
 *
 * Also hosts `runOnChatTab`, the "be on the chat tab, then act" helper the
 * footer-panel and transcript-navigation commands share.
 */

import { nextTick } from 'vue'

// Route names whose active tab IS the chat surface (the footer accordion lives
// here). The non-chat session sub-tabs (files/git/terminal/subagent) keep this
// panel mounted but hidden, so reaching a footer panel from them needs a route
// switch first.
const SESSION_CHAT_ROUTE_NAMES = new Set(['session', 'projects-session'])

/**
 * Move to a footer accordion panel (message input / pending request / embedded
 * terminal) by dispatching a ``twicc:goto-*`` window event, which the active
 * SessionItemsList applies (open the panel, reduce the others, take focus).
 *
 * The footer only exists on the chat tab, so when we're on another session
 * sub-tab we navigate there first and dispatch once the route settles — the
 * same navigate-then-act flow as Alt+Shift+M. Used by the global Alt+Shift+
 * {PageUp/PageDown/T} shortcuts and the matching command-palette actions.
 *
 * @param {import('vue-router').RouteLocationNormalizedLoaded} route
 * @param {import('vue-router').Router} router
 * @param {string} eventName - 'twicc:goto-message-input' | 'twicc:goto-pending-request' | 'twicc:goto-terminal'
 */
export function gotoChatFooterPanel(route, router, eventName) {
    runOnChatTab(route, router, () => window.dispatchEvent(new CustomEvent(eventName)))
}

/**
 * Run an action on the chat tab, navigating there first when we are on another
 * session sub-tab (the chat panel stays mounted but hidden there, so acting on
 * it from the Files tab would scroll something nobody can see).
 *
 * The action runs one tick after the route settles, so the panel is shown again
 * before it measures anything.
 *
 * @param {import('vue-router').RouteLocationNormalizedLoaded} route
 * @param {import('vue-router').Router} router
 * @param {() => void} action
 */
export function runOnChatTab(route, router, action) {
    if (SESSION_CHAT_ROUTE_NAMES.has(route.name)) {
        action()
        return
    }
    const chatRouteName = route.name.startsWith('projects-session') ? 'projects-session' : 'session'
    // `query` carried explicitly: the guard only re-propagates ?workspace= when the target
    // project belongs to it, so a session viewed from outside the workspace would otherwise
    // lose it on the way to its own Chat tab.
    router
        .push({ name: chatRouteName, params: route.params, query: route.query })
        .then(() => nextTick(action))
}

/**
 * Returns the primary interactive target inside the active PendingRequestForm,
 * or null when no pending request is shown. Picks the element that "I want to
 * act on right now" maps to:
 *   - tool_approval initial    → Approve button (carries .auto-focused)
 *   - tool_approval deny mode  → deny reason textarea
 *   - tool_approval edit mode  → "Approve with changes" button (sole brand button left)
 *   - ask_user_question        → first option-card
 */
export function getPendingRequestPrimaryTarget() {
    const form = document.querySelector('.pending-request-form')
    // No form, or it is minimized to its header (its body — and therefore every
    // actionable control — is display:none). In the minimized case the user has
    // expanded the composer alongside it, so it is no longer the primary target.
    if (!form || form.classList.contains('minimized')) return null
    if (form.querySelector('.questions-container')) {
        return form.querySelector('.option-card')
    }
    return (
        form.querySelector('wa-button.auto-focused')
        || form.querySelector('.deny-reason-input')
        || form.querySelector('.pending-request-actions wa-button[variant="brand"]')
    )
}

/**
 * Focus the chat's primary interactive element. When a pending request is
 * active, that's the appropriate control inside the form (Approve button,
 * deny textarea, …); otherwise it's the message input textarea.
 *
 * Retries because the caller may invoke this right after a router.push() or
 * a wa-tab-show event — even when the target is already in the DOM
 * (keep-alive), the transition can re-blur it as Vue re-renders. We keep
 * trying until focus actually sticks, or the retry budget runs out (~1.5s).
 */
export function focusChatPrimary(retries = 30) {
    // When a pending request is the primary target, focus it as-is — do NOT touch
    // the composer (on a main session it now sits collapsed alongside the request;
    // auto-expanding it here would wrongly minimize the request the user must
    // answer). Only when there is no request target is the composer primary: a
    // collapsed textarea is display:none and can't take focus, so ask it to expand
    // first (the retry below makes the focus stick).
    const formTarget = getPendingRequestPrimaryTarget()
    if (!formTarget) {
        document
            .querySelector('.message-input.collapsed')
            ?.dispatchEvent(new CustomEvent('twicc:expand-composer'))
    }

    const target = formTarget || document.querySelector('.message-input wa-textarea')
    if (!target) {
        if (retries > 0) setTimeout(() => focusChatPrimary(retries - 1), 50)
        return
    }
    target.focus()
    if (document.activeElement !== target && retries > 0) {
        setTimeout(() => focusChatPrimary(retries - 1), 50)
    }
}
