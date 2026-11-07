import { i18n } from '../i18n/index.js'
/**
 * useProviderActivation - Centralized enable/disable provider flow.
 *
 * Single source of truth for the rules that gate provider activation
 * (transition in progress, last provider standing, active sessions) and
 * for the actual mutation that toggles a provider on / off.
 *
 * The mutation goes through ``settingsStore.disabledProviders`` (the watcher
 * on that ref fans out the ``update_synced_settings`` WS message — same path
 * as the SettingsPopover switch).
 *
 * Consumers:
 *   - SettingsPopover (Providers section toggles)
 *   - ProviderAuthToastContent (Disable button)
 *   - ProjectView sidebar callout (Disable button)
 */
import { useSettingsStore } from '../stores/settings'
import { useDataStore } from '../stores/data'

function isInTransition(provider) {
    const state = useDataStore().getProviderState(provider)
    return state === 'starting' || state === 'stopping'
}

function isLastEnabled(provider) {
    const enabled = useSettingsStore().enabledProviders
    return enabled.length === 1 && enabled.includes(provider)
}

function hasActiveSession(provider) {
    return useDataStore().hasActiveSessionForProvider(provider)
}

/**
 * Whether the provider can be disabled right now.
 *
 * Mirrors the gate previously inlined in SettingsPopover.isSwitchDisabled
 * for the "currently enabled" branch: a provider can be disabled iff it is
 * not transitioning, not the only one left, and has no active sessions.
 */
function canDisableProvider(provider) {
    if (isInTransition(provider)) return false
    if (isLastEnabled(provider)) return false
    if (hasActiveSession(provider)) return false
    return true
}

/**
 * Human-readable reason a provider cannot be disabled, or null if it can.
 *
 * Transition states intentionally return null — the SettingsPopover shows
 * them via a dedicated spinner/label, not a danger hint.
 */
function disableReasonFor(provider) {
    if (isInTransition(provider)) return null
    if (isLastEnabled(provider)) return i18n.global.t('settings.providers.disableLastReason')
    if (hasActiveSession(provider)) return i18n.global.t('settings.providers.disableActiveReason')
    return null
}

/**
 * Whether the provider can be enabled right now.
 *
 * Mirrors the gate previously inlined in SettingsPopover.isSwitchDisabled
 * for the "currently disabled" branch: a provider can be re-enabled at any
 * time unless a lifecycle transition is in progress.
 */
function canEnableProvider(provider) {
    return !isInTransition(provider)
}

/**
 * Toggle a provider on or off.
 *
 * Mutates ``settingsStore.disabledProviders``; the store watcher takes care
 * of broadcasting the change via WS and starting / stopping the provider's
 * orchestrator backend-side.
 *
 * No-op if the requested state is already the current state, or if the
 * relevant gate (canEnable / canDisable) refuses the change. Callers that
 * need to react to a refusal should consult ``canDisableProvider`` /
 * ``canEnableProvider`` themselves before calling.
 */
function setProviderEnabled(provider, enabled) {
    const store = useSettingsStore()
    const current = new Set(store.disabledProviders || [])
    const alreadyEnabled = !current.has(provider)
    if (enabled === alreadyEnabled) return
    if (enabled && !canEnableProvider(provider)) return
    if (!enabled && !canDisableProvider(provider)) return
    if (enabled) current.delete(provider)
    else current.add(provider)
    store.disabledProviders = [...current]
}

export function useProviderActivation() {
    return {
        isInTransition,
        isLastEnabled,
        hasActiveSession,
        canDisableProvider,
        canEnableProvider,
        disableReasonFor,
        setProviderEnabled,
    }
}
