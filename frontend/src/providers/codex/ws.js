// frontend/src/providers/codex/ws.js
//
// Codex provider WebSocket surface — outbound senders + inbound dispatcher.
// Mirrors ``providers/claude_code/ws.js`` so the registry pattern in
// ``providers/index.js`` stays uniform.

import { sendWsMessage } from '../../composables/useWebSocket'
import { useCodexStore } from './store'

// ─── Outbound senders ────────────────────────────────────────────────────

/**
 * Force the backend to re-check Codex CLI auth state and broadcast the
 * result back via ``codex:auth_updated``.
 * @returns {boolean} - True if message was sent
 */
export function sendCheckAuth() {
    return sendWsMessage({ type: 'codex:check_auth' })
}

/**
 * Ask the backend to refresh the Codex usage snapshot now, allowing the OAuth
 * token refresh that the macOS background loop skips when Codex is in keyring
 * storage mode. The result comes back as a ``usage_updated`` message with
 * ``reason: "manual"``.
 * @returns {boolean} - True if message was sent
 */
export function sendCheckUsage() {
    return sendWsMessage({ type: 'codex:check_usage' })
}

/**
 * Respond to a pending Codex tool-approval request raised by the SDK
 * via the sync ↔ async bridge in CodexAgent. The ``responseData`` shape
 * must already match the Codex wire format (see backend spec §9.3/§9.5):
 *
 *   commandExecution / fileChange:
 *     { tool_name: 'commandExecution' | 'fileChange',
 *       decision: 'accept' | 'acceptForSession' | 'decline' | 'cancel'
 *                | { acceptWithExecpolicyAmendment: {...} }
 *                | { applyNetworkPolicyAmendment: {...} } }
 *
 *   permissions:
 *     { tool_name: 'permissions',
 *       permissions: {...}, scope: 'turn' | 'session',
 *       strictAutoReview?: boolean }
 *
 *   mcpToolCall / elicitationForm / elicitationUrl:
 *     { tool_name, action: 'accept' | 'decline' | 'cancel',
 *       persist?: 'session' | 'always',      // mcpToolCall accept only
 *       content?: { ... } }                  // elicitationForm accept only
 *
 *   toolRequestUserInput:
 *     { tool_name: 'toolRequestUserInput',
 *       answers: { [questionId]: { answers: [string, ...] } } }
 *
 *   autoReviewDenial:
 *     { tool_name: 'autoReviewDenial', decision: 'accept' | 'decline' }
 *
 * The backend ``CodexWSHandler._build_codex_response`` validates this
 * strictly and falls back to a safe default on any malformed payload, so
 * the SDK is never left waiting.
 *
 * @returns {boolean} True if the message was sent.
 */
export function respondToPendingRequest(sessionId, requestId, responseData) {
    return sendWsMessage({
        type: 'codex:pending_request_response',
        session_id: sessionId,
        request_id: requestId,
        ...responseData,
    })
}

// ─── Inbound handler ─────────────────────────────────────────────────────

/**
 * Dispatch a ``codex:<action>`` payload to its handler.
 * Called from the generic ``useWebSocket`` dispatcher.
 */
export const codexWsHandler = {
    handle(action, msg) {
        switch (action) {
            case 'auth_updated':
                useCodexStore().setAuthenticated(msg.authenticated)
                break
            default:
                console.warn(`[codex:ws] no handler for action "${action}"`, msg)
        }
    },
}
