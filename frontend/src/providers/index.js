import { i18n } from '../i18n/index.js'
import { ClaudeCodeHelpers, claudeCodeHelpers } from './claude_code/helpers'
import { ClaudeCodeToolHelpers, claudeCodeToolHelpers } from './claude_code/toolHelpers'
import { claudeCodeWsHandler, respondToPendingRequest as respondToClaudeCodePendingRequest } from './claude_code/ws'
import { useClaudeCodeStore } from './claude_code/store'
import { CodexHelpers, codexHelpers } from './codex/helpers'
import { CodexToolHelpers, codexToolHelpers } from './codex/toolHelpers'
import { codexWsHandler, respondToPendingRequest as respondToCodexPendingRequest } from './codex/ws'
import { useCodexStore } from './codex/store'

const PROVIDER_HELPERS = {
    [ClaudeCodeHelpers.provider]: claudeCodeHelpers,
    [CodexHelpers.provider]: codexHelpers,
}

const PROVIDER_TOOL_HELPERS = {
    [ClaudeCodeToolHelpers.provider]: claudeCodeToolHelpers,
    [CodexToolHelpers.provider]: codexToolHelpers,
}

const PROVIDER_WS_HANDLERS = {
    [ClaudeCodeHelpers.provider]: claudeCodeWsHandler,
    [CodexHelpers.provider]: codexWsHandler,
}

// Pinia store factory per provider — used by generic dispatchers (bootstrap
// seed, ``usage_updated`` WS) to write into the right provider-scoped store
// without knowing which provider it is.
const PROVIDER_STORE_FACTORIES = {
    [ClaudeCodeHelpers.provider]: useClaudeCodeStore,
    [CodexHelpers.provider]: useCodexStore,
}

export function getProviderHelpers(provider) {
    return PROVIDER_HELPERS[provider] ?? null
}

export function getToolHelpers(provider) {
    return PROVIDER_TOOL_HELPERS[provider] ?? null
}

export function getProviderWsHandler(provider) {
    return PROVIDER_WS_HANDLERS[provider] ?? null
}

export function getProviderStore(provider) {
    const factory = PROVIDER_STORE_FACTORIES[provider]
    return factory ? factory() : null
}

export function getRegisteredProviders() {
    return Object.keys(PROVIDER_HELPERS)
}

/**
 * Returns the subset of registered providers that are currently enabled
 * (i.e. not listed in the settings store's ``disabledProviders``).
 *
 * Cycle note: ``stores/settings.js`` statically imports ``getRegisteredProviders``
 * from this file. A static ``useSettingsStore`` import here would create a
 * mutual dependency that breaks Vite HMR. The getter is therefore defined on
 * the settings store itself (``enabledProviders``) and accessed here via a
 * lazy dynamic import — the import resolves synchronously from the module
 * cache after the first call, so there is no real async overhead.
 *
 * Callers MUST be in a Pinia-active scope (computed property, store action,
 * component render function, or any code that runs after ``createPinia()``).
 */
export async function getEnabledProviders() {
    const { useSettingsStore } = await import('../stores/settings')
    return useSettingsStore().enabledProviders
}

/**
 * Human-readable label for the given provider, suitable for inlining in
 * user-facing strings (toasts, tooltips, placeholders). Falls back to
 * ``'Agent'`` when the provider is unknown so callers don't have to guard.
 */
export function getProviderLabel(provider) {
    return PROVIDER_HELPERS[provider]?.constructor.label ?? i18n.global.t('agentSettings.agent')
}

/**
 * Web Awesome icon name (``<wa-icon name="...">``) for the given provider.
 * Resolved from each helpers class's ``static icon`` field, so adding a new
 * provider only requires declaring it on the new helpers — no constants.js
 * edit needed. Returns ``null`` when the provider is unknown or doesn't
 * declare an icon.
 */
export function getProviderIcon(provider) {
    return PROVIDER_HELPERS[provider]?.constructor.icon ?? null
}

/**
 * Brand tint (any CSS colour string) for the given provider's icon, declared
 * alongside the icon via the helpers' static ``iconColor`` field. Returns
 * ``null`` when the provider is unknown or declares no colour (inherit).
 */
export function getProviderIconColor(provider) {
    return PROVIDER_HELPERS[provider]?.constructor.iconColor ?? null
}

/**
 * ``[{ value, label }]`` pairs for every registered provider, intended to feed
 * a ``<wa-select>`` (e.g. the global default-provider setting). The label
 * comes from the helpers' static ``label`` field, falling back to the wire
 * key when a provider hasn't declared one.
 */
export function getProviderOptions() {
    return Object.entries(PROVIDER_HELPERS).map(([value, helpers]) => ({
        value,
        label: helpers.constructor.label ?? value,
    }))
}

const PENDING_REQUEST_SENDERS = {
    [ClaudeCodeHelpers.provider]: respondToClaudeCodePendingRequest,
    [CodexHelpers.provider]: respondToCodexPendingRequest,
}

/**
 * Provider-agnostic dispatcher for resolving a pending tool-approval /
 * ask-user-question request. Routes to the matching provider's outbound
 * sender. Throws if ``provider`` is unknown — callers should always
 * provide a registered provider (typically from ``session.provider``).
 *
 * ``responseData`` is provider-specific:
 * - claude_code: { request_type: 'tool_approval' | 'ask_user_question',
 *                  decision?: 'allow' | 'deny', updated_input?, updated_permissions?,
 *                  message?, action?: 'submit' | 'partial' | 'cancel', answers? }
 * - codex:       { tool_name: 'commandExecution' | 'fileChange' | 'permissions',
 *                  decision?: <string|dict>, permissions?, scope?, strictAutoReview? }
 *
 * @returns {boolean} True if the message was sent.
 */
export function respondToPendingRequest(provider, sessionId, requestId, responseData) {
    const sender = PENDING_REQUEST_SENDERS[provider]
    if (!sender) {
        throw new Error(`respondToPendingRequest: unknown provider ${provider}`)
    }
    return sender(sessionId, requestId, responseData)
}
