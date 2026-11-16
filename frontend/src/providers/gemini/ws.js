// frontend/src/providers/gemini/ws.js
//
// Gemini provider WebSocket surface — outbound senders + inbound dispatcher.
// Mirrors ``providers/codex/ws.js`` so the registry pattern in
// ``providers/index.js`` stays uniform.

import { sendWsMessage } from '../../composables/useWebSocket'

// ─── Outbound senders ────────────────────────────────────────────────────

/**
 * Respond to a pending Gemini tool-approval request (an ACP
 * ``session/request_permission``). The answer is one of the options the
 * request offered (``tool_input.options[].optionId``):
 *
 *   { option_id: 'proceed_once' | 'proceed_always' | 'cancel' | … }
 *
 * @returns {boolean} True if the message was sent.
 */
export function respondToPendingRequest(sessionId, requestId, responseData) {
    return sendWsMessage({
        type: 'gemini:pending_request_response',
        session_id: sessionId,
        request_id: requestId,
        option_id: responseData?.option_id ?? null,
    })
}

// ─── Inbound handler ─────────────────────────────────────────────────────

/**
 * Dispatch a ``gemini:<action>`` payload to its handler.
 * Called from the generic ``useWebSocket`` dispatcher. The Gemini backend
 * pushes no provider-specific message in v1 (no auth state, no usage).
 */
export const geminiWsHandler = {
    handle(action, msg) {
        switch (action) {
            default:
                console.warn(`[gemini:ws] no handler for action "${action}"`, msg)
        }
    },
}
