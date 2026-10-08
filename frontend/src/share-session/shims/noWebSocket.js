// The transcript tree statically imports many helpers from composables/useWebSocket
// (ToolUseContent, SessionToastContent, providers/*/ws.js, …). Read-only mode never
// invokes them; export no-op stubs so every import resolves without pulling the SPA
// WebSocket layer. (Enumerated from every `from '…/composables/useWebSocket'` import.)
import { ref } from 'vue'

export function killProcess() {}
export function sendWsMessage() {}
export function stopSubagent() {}
export function interruptSession() {}
export function requestTitleSuggestion() {}
export function notifyProcessStateChange() {}
export function clearUserTurnToast() {}
export function markSessionReadState() {}
export function notifySessionViewed() {}
export function forceNotifySessionViewed() {}
export function cancelSessionViewedThrottle() {}
export function notifyUserDraftUpdated() {}
export function sendChangelogSeen() {}
export function sendValidateTmuxConfigPath() {}
export function sendValidateUsageDumpPath() {}
export function sendValidateUsageFile() {}
export function useWebSocket() { return {} }
export const versionMismatchDetected = ref(false)
export const isConnected = { value: false }
