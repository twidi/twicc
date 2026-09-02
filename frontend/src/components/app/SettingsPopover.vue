<script setup>
// SettingsPopover.vue - Settings button with popover panel
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore, SETTINGS_SCHEMA } from '../../stores/settings'
import { useDataStore } from '../../stores/data'
import { useLayoutsStore } from '../../stores/layouts'
import { useAuthStore } from '../../stores/auth'
import { useTipsStore } from '../../stores/tips'
import { useHelpStore } from '../../stores/help'
import { usePeersStore } from '../../stores/peers'
import { getProviderHelpers, getProviderLabel, getProviderOptions, getRegisteredProviders, getProviderIcon } from '../../providers'
import ProviderIcon from '../ui/ProviderIcon.vue'
import { getActivationCharMetadata } from '../../utils/commandActivation'
import { validateWorktreeTemplate } from '../../utils/worktreePath'
import { useOriginSettingsForm } from '../../composables/useOriginSettingsForm'
import { usePeerSystemConfigured } from '../../composables/usePeerSystemConfigured'
import { DISPLAY_MODE, COLOR_SCHEME, SESSION_TIME_FORMAT, DEFAULT_MAX_CACHED_SESSIONS, WA_THEME, WA_THEME_LABELS, WA_BRAND, WA_BRAND_LABELS, SPONSOR_URL } from '../../constants'
import NotificationSettings from './NotificationSettings.vue'
import TipsSettings from '../settings/TipsSettings.vue'
import HelpSettings from '../settings/HelpSettings.vue'
import HelpIconButton from '../help/HelpIconButton.vue'
import PeerHelpLink from '../peer/PeerHelpLink.vue'
import PeerInboxBadge from '../peer/PeerInboxBadge.vue'
import { showHelp } from '../help/showHelp'
import AppTooltip from '../ui/AppTooltip.vue'
import ChangelogDialog from './ChangelogDialog.vue'
import LayoutManagerDialog from '../session/layout/LayoutManagerDialog.vue'
import ProviderSettingsSection from './ProviderSettingsSection.vue'
import ShareManagerDialog from '../share/ShareManagerDialog.vue'
import TelemetryPayloadDialog from './TelemetryPayloadDialog.vue'
import { sendChangelogSeen, sendValidateUsageDumpPath, sendValidateUsageFile, sendValidateTmuxConfigPath, requestTelemetryInstanceIdReset } from '../../composables/useWebSocket'
import { toast } from '../../composables/useToast'
import { useProviderActivation } from '../../composables/useProviderActivation'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'

const router = useRouter()
const store = useSettingsStore()
const dataStore = useDataStore()
const layoutsStore = useLayoutsStore()
const authStore = useAuthStore()
const tipsStore = useTipsStore()
const helpStore = useHelpStore()
const peersStore = usePeersStore()

// Tips section is hidden from the nav (and the active-section watcher
// below redirects away from it) when no tip matches the current
// environment's constraints. Empty manifest, or all tips filtered out
// by platform / os / providers, → no entry. Reactive: re-evaluates when
// the manifest, the touch-device flag, the OS, or enabledProviders changes.
const availableTips = computed(() => tipsStore.getAvailableTips({
    platform: store._isTouchDevice ? 'mobile' : 'desktop',
    os: store.os,
    enabledProviders: store.enabledProviders,
}))
const hasTips = computed(() => availableTips.value.length > 0)

// Help section: same gating as tips — hidden from the nav when no help
// page matches the current environment's constraints.
const availableHelp = computed(() => helpStore.getAvailableHelp({
    platform: store._isTouchDevice ? 'mobile' : 'desktop',
    os: store.os,
    enabledProviders: store.enabledProviders,
}))
const hasHelp = computed(() => availableHelp.value.length > 0)

// The divider separates actual settings from utility sections. Shortcuts is
// unavailable on touch devices, but Tips or Help can still require the divider.
const hasUtilitySections = computed(() => !store.isTouchDevice || hasTips.value || hasHelp.value)

// Reactive set of currently enabled providers (derived from the settings store).
const enabledProviders = computed(() => new Set(store.enabledProviders))

// Show logout button only when password-based auth is active
const showLogout = computed(() => authStore.passwordRequired && authStore.authenticated)
const logoutButtonId = useId()

function handleLogout() {
    router.push({ name: 'logout' })
}

// -- Section navigation --

// Per-provider sections — one entry per registered provider, identified
// by ``provider_<key>`` so the activeSection check disambiguates them.
const providerSections = computed(() =>
    getRegisteredProviders().map(provider => {
        const helpers = getProviderHelpers(provider)
        const label = helpers.constructor.label ?? provider
        return {
            id: `provider_${provider}`,
            provider,
            label: `${label} settings`,
            navLabel: label,
            icon: getProviderIcon(provider),
            synced: true,
            enabled: enabledProviders.value.has(provider),
        }
    })
)

const sections = computed(() => [
    { id: 'general',       label: 'General' },
    { id: 'notifications', label: 'Notifications' },
    { id: 'providers',     label: 'Providers', synced: true },
    ...providerSections.value.filter(s => s.enabled),
    { id: 'sessions',      label: 'Sessions' },
    { id: 'layouts',       label: 'Layouts', synced: true },
    { id: 'title',         label: 'Title suggestion', navLabel: 'Titles', synced: true },
    { id: 'editor',        label: 'Editor' },
    { id: 'terminal',      label: 'Terminal' },
    { id: 'sharing',       label: 'Sharing', synced: true },
    { id: 'usage',         label: 'Providers quotas/usage', navLabel: 'Usage' },
    { id: 'peers',         label: 'Peers', synced: true, badge: peersStore.inboxCount },
])

const activeSection = ref('general')
const mobileShowContent = ref(false)
const popoverRef = ref(null)

// If the user is sitting on the Tips section when its nav entry
// disappears (e.g. they just toggled the last enabled provider that
// gated the only available tip), bounce them back to General so the
// detail panel doesn't render an empty/orphaned TipsSettings.
watch(hasTips, (now) => {
    if (!now && activeSection.value === 'tips') {
        activeSection.value = 'general'
    }
})

// Same bounce for the Help section when its nav entry disappears.
watch(hasHelp, (now) => {
    if (!now && activeSection.value === 'help') {
        activeSection.value = 'general'
    }
})

function handleCloseRequest() {
    const el = popoverRef.value
    if (!el) return
    if (typeof el.hide === 'function') {
        el.hide()
    } else {
        // Fallback: toggle the open attribute. Web Awesome 3 wa-popover should
        // expose hide(); this is a defensive path.
        el.removeAttribute('open')
    }
}

onMounted(() => {
    window.addEventListener('twicc:close-settings-popover', handleCloseRequest)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:close-settings-popover', handleCloseRequest)
})

const activeSectionObj = computed(() =>
    sections.value.find(s => s.id === activeSection.value)
)

const activeSectionLabel = computed(() => {
    if (activeSection.value === 'shortcuts') return 'Keyboard shortcuts'
    if (activeSection.value === 'tips') return 'Tips'
    if (activeSection.value === 'help') return 'Help'
    return activeSectionObj.value?.label ?? ''
})

function selectSection(id) {
    activeSection.value = id
    mobileShowContent.value = true
    if (id === 'general') {
        worktreeDirInput.value = worktreeDirectoryTemplate.value || ''
        seedOriginField('publicBaseUrl')
    }
    if (id === 'sharing') {
        seedOriginField('shareBaseUrl')
    }
    if (id === 'peers') {
        seedOriginField('peerBaseUrl')
        peerDisplayNameInput.value = store.getPeerDisplayName || ''
    }
    if (id === 'notifications') {
        nextTick(() => notificationSettingsRef.value?.sync())
    }
    if (id === 'usage') {
        // Seed local input/validation state from persisted values, one
        // entry per provider that tracksUsage.
        const fileInputs = {}
        const fileValidations = {}
        const dumpInputs = {}
        const dumpValidations = {}
        for (const provider of usageProviders.value) {
            fileInputs[provider] = getReadPath(provider)
            fileValidations[provider] = null
            dumpInputs[provider] = getDumpPath(provider)
            dumpValidations[provider] = null
        }
        usageFilePathInput.value = fileInputs
        usageFileValidation.value = fileValidations
        usageDumpPathInput.value = dumpInputs
        usageDumpValidation.value = dumpValidations
    }
    if (id === 'terminal') {
        tmuxConfigPathInput.value = terminalTmuxConfigPath.value || ''
        tmuxConfigValidation.value = null
    }
    if (id === 'title') {
        titleSystemPromptInput.value = titleSystemPrompt.value
    }
}

function goBackToNav() {
    mobileShowContent.value = false
}

// -- Keyboard shortcuts data --

// The session switcher cycles on the *physical* key above Tab (e.code
// 'Backquote'), which carries a different legend per layout (`` ` `` on QWERTY,
// `²` on AZERTY, `^` on QWERTZ…). Default to the QWERTY legend, then — on
// Chromium, the only engine exposing the Keyboard Map API — refine it to the
// actual character printed on the user's keyboard. Elsewhere the static label
// plus the "key above Tab" wording in the description keep it unambiguous.
const backquoteKeyLabel = ref('`')
onMounted(async () => {
    try {
        const layoutMap = await navigator.keyboard?.getLayoutMap?.()
        const label = layoutMap?.get('Backquote')
        if (label) backquoteKeyLabel.value = label
    } catch {
        // API unsupported or rejected — the QWERTY default stands on purpose.
    }
})

const shortcutGroups = computed(() => {
    const mod = store.isMac ? '⌘' : 'Ctrl'

    // Collect command activation chars across every enabled provider so
    // the cheat sheet stays in sync with what the message input actually
    // reacts to. Disabled providers are skipped — their activation chars
    // don't open the picker. A char shared by several providers (e.g.
    // ``/`` may be claimed by both Claude Code and Codex) collapses to a
    // single row whose description lists every provider that handles it.
    const charToProviderLabels = new Map()
    for (const provider of getRegisteredProviders()) {
        if (!enabledProviders.value.has(provider)) continue
        const helpers = getProviderHelpers(provider)
        const label = getProviderLabel(provider)
        for (const char of helpers?.getCommandActivationChars() ?? []) {
            if (!charToProviderLabels.has(char)) charToProviderLabels.set(char, [])
            const labels = charToProviderLabels.get(char)
            if (!labels.includes(label)) labels.push(label)
        }
    }

    const commandShortcuts = []
    for (const [char, labels] of charToProviderLabels.entries()) {
        const meta = getActivationCharMetadata(char)
        if (!meta) continue
        commandShortcuts.push({
            keys: [char],
            description: `${meta.tooltip} (at start of input — ${labels.join(', ')})`,
        })
    }

    return [
        {
            label: 'Global',
            shortcuts: [
                { keys: [mod, 'K'], description: 'Open command palette' },
                { keys: [mod, 'Shift', 'F'], description: 'Open full-text search' },
                // Always Ctrl (even on macOS, where ⌘+` is an OS shortcut), so these
                // deliberately don't use `mod`. The cycle key is the physical key
                // above Tab — backquoteKeyLabel resolves its legend per layout, and
                // the description names its position too.
                { keys: ['Ctrl', backquoteKeyLabel.value], description: 'Switch between recent sessions — hold Ctrl, tap the key above Tab (or ↑/↓) to cycle, release to switch' },
                { keys: ['Ctrl', 'Shift', backquoteKeyLabel.value], description: 'Same, through the sessions currently shown in the sidebar (tap Shift while open to toggle either source)' },
                { keys: ['Alt', 'Shift', 'B'], description: 'Toggle the sidebar' },
            ]
        },
        {
            label: 'Session tabs',
            shortcuts: [
                { keys: ['Alt', 'Shift', '1–9, 0'], description: 'Jump to tab (Chat, Files, Git, Terminal, Tasks, Plan, Artifacts, Orchestration, Workflows, Browser)' },
                { keys: ['Alt', 'Shift', '←/→'], description: 'Previous / next tab' },
                { keys: ['Alt', 'Shift', '↑/↓'], description: 'Last visited tab' },
            ]
        },
        {
            label: 'Session chat',
            shortcuts: [
                { keys: ['Alt', 'Shift', 'M'], description: 'Focus message input — or the active pending request when one is open (from any session tab)' },
                { keys: ['Alt', 'Shift', 'PageDown'], description: 'Go to the message input (chat tab)' },
                // PageUp is dual-purpose: pending-request nav always, plus the hybrid
                // CLI terminal only when hybrid mode is enabled — drop that clause off.
                store.isClaudeHybridEnabled
                    ? { keys: ['Alt', 'Shift', 'PageUp'], description: 'Go to the open pending request — or, from the message input on a hybrid session, the Claude CLI terminal (chat tab)' }
                    : { keys: ['Alt', 'Shift', 'PageUp'], description: 'Go to the open pending request (chat tab)' },
                // Hybrid-only chords: listed only when hybrid mode is enabled.
                ...(store.isClaudeHybridEnabled ? [
                    { keys: ['Alt', 'Shift', 'T'], description: 'Toggle between the Claude CLI terminal and the message input — hybrid sessions only (from any session tab)' },
                    { keys: ['Alt', 'Shift', 'H'], description: 'Toggle hybrid mode on a Claude session, where it can be toggled (from any session tab)' },
                ] : []),
                { keys: ['Quick triple Esc'], description: 'Emergency stop of the running process' },
                { keys: ['Shift', 'Quick triple Esc'], description: 'Force kill the running process — no grace window, no confirmation (also: Shift-click the Stop button, or click it again while stopping)' },
            ]
        },
        {
            label: 'Session layout',
            shortcuts: [
                { keys: ['Alt', 'Shift', '↵'], description: 'Maximize the focused pane (chat or a docked tool panel) — press again to restore' },
                { keys: ['Alt', 'Shift', 'Backspace'], description: 'Minimize the focused docked panel to the gutter' },
            ]
        },
        {
            label: 'Message input',
            shortcuts: [
                { keys: [mod, '↵'], description: 'Send message' },
                { keys: ['Alt', 'Shift', 'O'], description: 'Open / close Agent Settings popover' },
                { keys: ['@'], description: 'Insert file path (after a space or at start)' },
                ...commandShortcuts,
                { keys: ['!'], description: 'Message history (at start of input)' },
                { keys: ['PageUp'], description: 'Message history (cursor on first line)' },
            ]
        },
        {
            label: 'Pending request (approval / question)',
            shortcuts: [
                { keys: [mod, '↵'], description: 'Submit the form — Approve (or "Approve with changes" in edit mode, Submit for a question). Sends Deny / Cancel turn when that button is focused.' },
            ]
        },
        {
            label: 'Project home tabs',
            shortcuts: [
                { keys: ['Alt', 'Shift', '1–4'], description: 'Jump to tab (Stats, Files, Git, Terminal)' },
                { keys: ['Alt', 'Shift', '←/→'], description: 'Previous / next tab' },
                { keys: ['Alt', 'Shift', '↑/↓'], description: 'Last visited tab' },
            ]
        },
        {
            label: 'Terminal tabs',
            shortcuts: [
                { keys: ['Alt', 'Ctrl', 'Shift', '1–9'], description: 'Jump to terminal tab N' },
                { keys: ['Alt', 'Ctrl', 'Shift', '←/→'], description: 'Previous / next terminal tab' },
                { keys: ['Alt', 'Ctrl', 'Shift', '↑/↓'], description: 'Last visited terminal tab' },
            ]
        },
        {
            label: 'Workflow run tabs',
            shortcuts: [
                { keys: ['Alt', 'Ctrl', 'Shift', '1–9'], description: 'Jump to workflow run tab N' },
                { keys: ['Alt', 'Ctrl', 'Shift', '←/→'], description: 'Previous / next workflow run tab' },
                { keys: ['Alt', 'Ctrl', 'Shift', '↑/↓'], description: 'Last visited workflow run tab' },
            ]
        },
        {
            label: 'In-session search',
            shortcuts: [
                { keys: [mod, 'F'], description: 'Find in current session' },
                { keys: ['F3'], description: 'Next match (works without focus)' },
                { keys: ['Shift', 'F3'], description: 'Previous match (works without focus)' },
            ]
        },
        {
            label: 'Files / Git editor',
            shortcuts: [
                { keys: ['Alt', 'E'], description: 'Toggle edit mode for the current file' },
            ]
        },
        {
            label: 'Terminal',
            shortcuts: [
                { keys: ['Ctrl', 'C'], description: 'Copy selected text (instead of SIGINT)' },
                { keys: ['Ctrl', 'Shift', 'C'], description: 'Copy selected text' },
                { keys: ['Ctrl', 'D'], description: 'Send EOF / disconnect' },
            ]
        },
    ]
})

// WA theme/palette/brand options
const waThemeOptions = Object.values(WA_THEME).map(value => ({
    value,
    label: WA_THEME_LABELS[value],
}))

const waBrandOptions = Object.values(WA_BRAND).map(value => ({
    value,
    label: WA_BRAND_LABELS[value],
}))

// Color scheme options for the select
const colorSchemeOptions = [
    { value: COLOR_SCHEME.SYSTEM, label: 'System' },
    { value: COLOR_SCHEME.LIGHT, label: 'Light' },
    { value: COLOR_SCHEME.DARK, label: 'Dark' },
]

// Session time format options for the select
const sessionTimeFormatOptions = [
    { value: SESSION_TIME_FORMAT.TIME, label: 'Time' },
    { value: SESSION_TIME_FORMAT.RELATIVE_SHORT, label: 'Relative (short)' },
    { value: SESSION_TIME_FORMAT.RELATIVE_NARROW, label: 'Relative (narrow)' },
]

const notificationSettingsRef = ref(null)
const changelogDialogRef = ref(null)
const forcedChangelogOpen = ref(false)

// Settings from store
const defaultProvider = computed(() => store.getDefaultProvider)
// Global default layout for new sessions: the picker value + the selectable list (Single pane + named).
const defaultLayoutId = computed(() => store.getDefaultLayoutId || 'single-pane')
const selectableLayouts = computed(() => layoutsStore.selectableLayouts)
const providerOptions = getProviderOptions()
const enabledProviderOptions = computed(() =>
    providerOptions.filter(opt => enabledProviders.value.has(opt.value))
)
function providerIconFor(provider) {
    return getProviderIcon(provider)
}

// ─── Provider activation helpers ─────────────────────────────────────

const { canDisableProvider, canEnableProvider, disableReasonFor, setProviderEnabled } = useProviderActivation()

function providerLabelFor(p) {
    return getProviderLabel(p)
}
function providerStateFor(p) {
    return dataStore.getProviderState(p)
}
function isSwitchDisabled(p) {
    // Enabled providers gate on the full disable check (transition, last,
    // active sessions). Disabled providers only gate on the transition.
    return enabledProviders.value.has(p) ? !canDisableProvider(p) : !canEnableProvider(p)
}
function reasonFor(p) {
    // Only enabled providers can be blocked from a disable attempt —
    // disabled providers either re-enable instantly or are gated by a
    // transition (shown as a spinner label, not a danger hint).
    if (!enabledProviders.value.has(p)) return null
    return disableReasonFor(p)
}
function transitionLabelFor(p) {
    const state = providerStateFor(p)
    if (state === 'starting') return 'Starting'
    if (state === 'stopping') return 'Stopping'
    return null
}
function onToggleProvider(p, event) {
    setProviderEnabled(p, event.target.checked)
}
const displayMode = computed(() => store.getDisplayMode)
const fontSize = computed(() => store.getFontSize)
const colorScheme = computed(() => store.getColorScheme)
const sessionTimeFormat = computed(() => store.getSessionTimeFormat)
const showCosts = computed(() => store.areCostsShown)
const telemetryEnabled = computed(() => store.isTelemetryEnabled)
const showTelemetryPayload = ref(false)
const extraUsageOnlyWhenNeeded = computed(() => store.isExtraUsageOnlyWhenNeeded)
// Same synced setting as the one in the Notifications section — mirrored here
// so a user browsing the Usage section doesn't miss the feature.
const notifyOnExtraUsageStart = computed(() => store.shouldNotifyOnExtraUsageStart)
const maxCachedSessions = computed(() => store.getMaxCachedSessions)
const autoUnpinOnArchive = computed(() => store.isAutoUnpinOnArchive)
const allowAgentSessionShares = computed(() => store.isAllowAgentSessionShares)
const allowAgentArtifactShares = computed(() => store.isAllowAgentArtifactShares)
const titleGenerationEnabled = computed(() => store.isTitleGenerationEnabled)
const titleAutoApply = computed(() => store.isTitleAutoApply)
const titleSystemPrompt = computed(() => store.getTitleSystemPrompt)
const titleSystemPromptInput = ref('')

// "Haiku for Claude, GPT-5.6 Luna for Codex" — built from the enabled
// providers that name their pinned title model, so the sentence follows a
// model change (or a provider being turned off) instead of going stale.
const titleSuggestionModels = computed(() => {
    const parts = getRegisteredProviders()
        .filter(provider => enabledProviders.value.has(provider))
        .map(provider => {
            const helpers = getProviderHelpers(provider).constructor
            return helpers.titleSuggestionModelLabel
                ? `${helpers.titleSuggestionModelLabel} for ${helpers.label ?? provider}`
                : null
        })
        .filter(Boolean)
    return parts.length ? `Using ${parts.join(', ')}` : ''
})
const terminalUseTmux = computed(() => store.isTerminalUseTmux)
const terminalTmuxConfigPath = computed(() => store.getTerminalTmuxConfigPath)
const terminalMacOptionIsMeta = computed(() => store.isTerminalMacOptionIsMeta)
const terminalCopyOnSelect = computed(() => store.isTerminalCopyOnSelect)
const isMac = computed(() => store.isMac)
const isLinux = computed(() => store.isLinux)
const worktreeDirectoryTemplate = computed(() => store.getWorktreeDirectoryTemplate)
const compactSessionList = computed(() => store.isCompactSessionList)
const showMessageTimestamps = computed(() => store.areMessageTimestampsShown)
const waTheme = computed(() => store.getWaTheme)
const waBrand = computed(() => store.getWaBrand)
const showDiffs = computed(() => store.isShowDiffs)
const toolDiffWordWrap = computed(() => store.isToolDiffWordWrap)
const toolDiffSideBySide = computed(() => store.isToolDiffSideBySide)
const diffSideBySide = computed(() => store.isDiffSideBySide)
const editorWordWrap = computed(() => store.isEditorWordWrap)
// ─── Usage section: per-provider state (cross-provider) ─────────────
//
// Every registered provider that ``tracksUsage()`` may surface read +
// dump file settings. The Settings popover renders one block per
// provider, so all of read/dump local state (input value, in-flight
// validating flag, last validation result) is keyed by provider here.
// Persisted values live on each provider's own store, accessed via
// ``getProviderHelpers(provider).getUsageFileSetting(field)``.

const usageProviders = computed(() =>
    getRegisteredProviders().filter(
        p => enabledProviders.value.has(p) && getProviderHelpers(p)?.tracksUsage()
    )
)

// Reactive maps keyed by provider wire key.
const usageFilePathInput = ref({})         // { [provider]: string }
const usageFileValidating = ref({})        // { [provider]: boolean }
const usageFileValidation = ref({})        // { [provider]: {valid,message} | null }

const usageDumpPathInput = ref({})
const usageDumpValidating = ref({})
const usageDumpValidation = ref({})

function getReadEnabled(provider) {
    return !!getProviderHelpers(provider)?.getUsageFileSetting('read_enabled')
}
function getReadPath(provider) {
    return getProviderHelpers(provider)?.getUsageFileSetting('read_path') || ''
}
function getDumpEnabled(provider) {
    return !!getProviderHelpers(provider)?.getUsageFileSetting('dump_enabled')
}
function getDumpPath(provider) {
    return getProviderHelpers(provider)?.getUsageFileSetting('dump_path') || ''
}

function isReadPathModified(provider) {
    return (usageFilePathInput.value[provider] ?? '').trim() !== getReadPath(provider)
}
function readApplyIcon(provider) {
    if (usageFileValidation.value[provider]?.valid === false) return 'x-circle'
    if (isReadPathModified(provider)) return 'triangle-exclamation'
    return 'check'
}
function isDumpPathModified(provider) {
    return (usageDumpPathInput.value[provider] ?? '').trim() !== getDumpPath(provider)
}
function dumpApplyIcon(provider) {
    if (usageDumpValidation.value[provider]?.valid === false) return 'x-circle'
    if (isDumpPathModified(provider)) return 'triangle-exclamation'
    return 'check'
}

// ─── Quota warm-up: hour/minute selectors (cross-provider) ───────────
//
// The warm-up time is stored as a single "HH:MM" string (empty = disabled)
// via ``helpers.getQuotaWakeupTime`` / ``setQuotaWakeupTime``. The UI splits
// it across two selects — hours 00–23 and minutes capped to 10-minute steps
// — so there is nothing to validate and the value is saved on every change
// (no Apply button). Picking "Off" in the hour select clears the setting.

// Hour options 00–23: the stored value is always the 24h "HH"; only the label
// is locale-aware (12h AM/PM or 24h, per the browser locale, like the app's
// other clocks). Built once — the locale doesn't change at runtime.
const WAKEUP_HOUR_OPTIONS = (() => {
    const fmt = new Intl.DateTimeFormat(navigator.language, { hour: 'numeric' })
    return Array.from({ length: 24 }, (_, h) => ({
        value: String(h).padStart(2, '0'),
        label: fmt.format(new Date(2000, 0, 1, h)),
    }))
})()
const WAKEUP_MINUTES = ['00', '10', '20', '30', '40', '50']

function supportsWakeup(provider) {
    return !!getProviderHelpers(provider)?.supportsQuotaWakeup()
}
function getWakeupHour(provider) {
    return (getProviderHelpers(provider)?.getQuotaWakeupTime() || '').split(':')[0] || ''
}
function getWakeupMinute(provider) {
    const minute = (getProviderHelpers(provider)?.getQuotaWakeupTime() || '').split(':')[1]
    return WAKEUP_MINUTES.includes(minute) ? minute : '00'
}
function onWakeupHourChange(provider, event) {
    const helpers = getProviderHelpers(provider)
    if (!helpers) return
    const hour = event.target.value
    // "Off" (empty) clears the whole setting; otherwise pair with the
    // currently-selected minute (defaulting to the top of the hour).
    helpers.setQuotaWakeupTime(hour ? `${hour}:${getWakeupMinute(provider)}` : '')
}
function onWakeupMinuteChange(provider, event) {
    const helpers = getProviderHelpers(provider)
    if (!helpers) return
    const hour = getWakeupHour(provider)
    // The minute select is disabled when no hour is set, so this only fires
    // with an hour selected; guard anyway.
    if (!hour) return
    helpers.setQuotaWakeupTime(`${hour}:${event.target.value}`)
}

// Tmux config path — local input + validation state
const tmuxConfigPathInput = ref('')
const tmuxConfigValidating = ref(false)
const tmuxConfigValidation = ref(null) // { valid: boolean, message: string } | null
const tmuxConfigPathModified = computed(() => tmuxConfigPathInput.value.trim() !== (terminalTmuxConfigPath.value || ''))
const tmuxConfigApplyIcon = computed(() => {
    if (tmuxConfigValidation.value?.valid === false) return 'x-circle'
    if (tmuxConfigPathModified.value) return 'triangle-exclamation'
    return 'check'
})

// Worktree directory template — local input, committed to the store on Apply
// only. Placeholders are validated live; an invalid template blocks Apply.
const worktreeDirInput = ref('')
const worktreeDirModified = computed(() => worktreeDirInput.value.trim() !== (worktreeDirectoryTemplate.value || ''))
const worktreeTemplateValidation = computed(() => validateWorktreeTemplate(worktreeDirInput.value.trim()))
const worktreeTemplateError = computed(() => {
    const v = worktreeTemplateValidation.value
    if (v.valid) return ''
    if (v.unknown.length) {
        const list = v.unknown.map((n) => `{${n}}`).join(', ')
        return `Unknown placeholder${v.unknown.length > 1 ? 's' : ''}: ${list}. `
            + 'Allowed: {git_root}, {project_name}, {project_basedir}.'
    }
    return 'Malformed template: check for unmatched { or } braces.'
})
const worktreeDirApplyIcon = computed(() => {
    if (!worktreeTemplateValidation.value.valid) return 'x-circle'
    return worktreeDirModified.value ? 'triangle-exclamation' : 'check'
})

// The three public-origin fields (External / Share / Peer). The wiring lives in
// the composable; the component keeps only the DOM refs it focuses.
// `publicBaseUrl` is the External address, `shareBaseUrl` the dedicated share
// host (design §12: a DIFFERENT hostname from this app, checked client-side
// because cookies are not port-scoped), `peerBaseUrl` the peer address — that
// one has no different-hostname restriction, /peer/ being a same-origin
// carve-out.
const {
    seedOriginField,
    startOriginSettingsForm,
    stopOriginSettingsForm,
    publicBaseUrlInput, publicBaseUrlError, publicBaseUrlApplyIcon,
    onPublicBaseUrlInputChange, onPublicBaseUrlApply,
    shareBaseUrlInput, shareBaseUrlError, shareBaseUrlApplyIcon,
    onShareBaseUrlInputChange, onShareBaseUrlApply,
    peerBaseUrlInput, peerBaseUrlError, peerBaseUrlWarning, peerBaseUrlConfirmation, peerBaseUrlApplyIcon,
    onPeerBaseUrlInputChange, onPeerBaseUrlApply, confirmPeerBaseUrlApply, cancelPeerBaseUrlApply,
    canPrefillPeerBaseUrl, prefillPeerBaseUrlFromPublic,
} = useOriginSettingsForm({
    settingsStore: store,
    dataStore,
    locationHostname: window.location.hostname,
    eventTarget: window,
})

const publicBaseUrlInputRef = ref(null)
const shareBaseUrlInputRef = ref(null)
const peerBaseUrlInputRef = ref(null)

// Whether the peer actions are worth showing — the same condition that decides
// whether the sidebar inbox button exists. See `usePeerSystemConfigured`.
const hasPeerActions = usePeerSystemConfigured()
const showShareManager = ref(false)

// Display name advertised to peers in handshakes; empty falls back to the
// hostname of peerBaseUrl (server-side).
const peerDisplayNameInput = ref('')
const peerDisplayNameModified = computed(() => peerDisplayNameInput.value.trim() !== (store.getPeerDisplayName || ''))
const peerDisplayNameApplyIcon = computed(() => (peerDisplayNameModified.value ? 'triangle-exclamation' : 'check'))

function onPeerDisplayNameInputChange(event) {
    peerDisplayNameInput.value = event.target.value
}

function onPeerDisplayNameApply() {
    store.setPeerDisplayName(peerDisplayNameInput.value)
    peerDisplayNameInput.value = store.getPeerDisplayName || ''
}

// Check if the current prompt is the default
const isDefaultPrompt = computed(() => titleSystemPrompt.value === SETTINGS_SCHEMA.titleSystemPrompt)
const isTitleSystemPromptModified = computed(() => titleSystemPromptInput.value !== titleSystemPrompt.value)
const titleSystemPromptApplyIcon = computed(() => (isTitleSystemPromptModified.value ? 'triangle-exclamation' : 'check'))

// Server info for footer
const currentVersion = computed(() => dataStore.currentVersion)
const latestVersion = computed(() => dataStore.latestVersion)

// ─── Service status footer rotation ──────────────────────────────────
//
// Providers that publish a public service status (e.g. Anthropic's
// statuspage for Claude Code) opt in via ``helpers.getServiceStatus()``
// and ``helpers.getServiceStatusDisplay()``. The footer rotates among
// them every ``STATUS_ROTATION_INTERVAL_MS`` to keep the chrome compact;
// hover pauses the rotation (mirrors the sidebar usage rotation).

const STATUS_ROTATION_INTERVAL_MS = 15000

const _statusAwareProviders = computed(() =>
    getRegisteredProviders()
        .filter(p => enabledProviders.value.has(p))
        .map(provider => ({
            provider,
            helpers: getProviderHelpers(provider),
            getter: getProviderHelpers(provider).getServiceStatus(),
        }))
        .filter(({ getter }) => getter !== null)
)

const currentStatusProviderIndex = ref(0)

const currentStatusProvider = computed(() => {
    if (_statusAwareProviders.value.length === 0) return null
    return _statusAwareProviders.value[currentStatusProviderIndex.value % _statusAwareProviders.value.length]
})

const currentStatusDisplay = computed(() => {
    const entry = currentStatusProvider.value
    if (!entry) return null
    const status = entry.getter()
    if (!status) return null
    return entry.helpers.getServiceStatusDisplay(status)
})

const currentStatusIcon = computed(() => {
    const entry = currentStatusProvider.value
    return entry ? getProviderIcon(entry.provider) : null
})

const hasMultipleStatusProviders = computed(() => _statusAwareProviders.value.length > 1)

const statusFooterId = useId()
const statusFooterRef = ref(null)
const statusNextButtonId = useId()

let _statusRotationTimer = null
function _scheduleStatusRotation() {
    if (_statusRotationTimer) clearInterval(_statusRotationTimer)
    if (_statusAwareProviders.value.length <= 1) return
    _statusRotationTimer = setInterval(() => {
        if (statusFooterRef.value && statusFooterRef.value.matches(':hover')) return
        currentStatusProviderIndex.value =
            (currentStatusProviderIndex.value + 1) % _statusAwareProviders.value.length
    }, STATUS_ROTATION_INTERVAL_MS)
}
function _stopStatusRotation() {
    if (_statusRotationTimer) {
        clearInterval(_statusRotationTimer)
        _statusRotationTimer = null
    }
}
function cycleStatusProvider() {
    const total = _statusAwareProviders.value.length
    if (total <= 1) return
    currentStatusProviderIndex.value = (currentStatusProviderIndex.value + 1) % total
    // Restart the timer so the freshly selected provider gets the full
    // rotation interval rather than whatever remained on the previous tick.
    _scheduleStatusRotation()
}
_scheduleStatusRotation()
onBeforeUnmount(_stopStatusRotation)

// Re-schedule the rotation whenever the set of status-aware enabled providers changes.
watch(
    () => _statusAwareProviders.value.length,
    (newLen) => {
        if (currentStatusProviderIndex.value >= newLen) {
            currentStatusProviderIndex.value = 0
        }
        _stopStatusRotation()
        if (newLen > 1) {
            _scheduleStatusRotation()
        }
    }
)

// Display mode options for the select
const displayModeOptions = [
    { value: DISPLAY_MODE.CONVERSATION, label: 'Conversation' },
    { value: DISPLAY_MODE.SIMPLIFIED, label: 'Simplified' },
    { value: DISPLAY_MODE.NORMAL, label: 'Detailed' },
    { value: DISPLAY_MODE.DEBUG, label: 'Debug' },
]


/** Handle the global default-layout change. */
function onDefaultLayoutChange(event) {
    store.setDefaultLayoutId(event.target.value)
}

const layoutManagerDialogRef = ref(null)
function onManageLayouts() {
    layoutManagerDialogRef.value?.open()
}

/**
 * Handle default-provider change.
 */
function onDefaultProviderChange(event) {
    store.setDefaultProvider(event.target.value)
}

/**
 * Handle display mode change.
 */
function onDisplayModeChange(event) {
    store.setDisplayMode(event.target.value)
}

/**
 * Handle font size slider change.
 */
function onFontSizeChange(event) {
    store.setFontSize(event.target.value)
}

function onColorSchemeChange(event) {
    store.setColorScheme(event.target.value)
}

function onWaThemeChange(event) {
    store.setWaTheme(event.target.value)
}

function onWaBrandChange(event) {
    store.setWaBrand(event.target.value)
}

// The origin form subscribes to the correlated-result event itself; the
// component only owns the lifecycle.
onMounted(startOriginSettingsForm)
onBeforeUnmount(stopOriginSettingsForm)

function openPeersManager() {
    window.dispatchEvent(new CustomEvent('twicc:open-peers-manager'))
}

function openPeerInbox() {
    window.dispatchEvent(new CustomEvent('twicc:open-peer-inbox'))
}

// Called when the Notifications section's callout is clicked: jump to General and
// focus the External address field.
function goToPublicBaseUrl() {
    selectSection('general')
    nextTick(() => publicBaseUrlInputRef.value?.focus())
}

/**
 * Handle session time format change.
 */
function onSessionTimeFormatChange(event) {
    store.setSessionTimeFormat(event.target.value)
}

/**
 * Toggle costs display.
 */
function onShowCostsChange(event) {
    store.setShowCosts(event.target.checked)
}

/**
 * Toggle anonymous telemetry.
 */
function onTelemetryEnabledChange(event) {
    store.setTelemetryEnabled(event.target.checked)
}

/**
 * Ask the backend to regenerate the anonymous telemetry instance id.
 */
async function resetTelemetryInstanceId() {
    const result = await requestTelemetryInstanceIdReset()
    if (result.instance_id) {
        toast.success('Telemetry instance ID reset')
    } else {
        toast.error('Failed to reset telemetry instance ID')
    }
}

/**
 * Toggle extra usage "only when needed" mode.
 */
function onExtraUsageOnlyWhenNeededChange(event) {
    store.setExtraUsageOnlyWhenNeeded(event.target.checked)
}

/**
 * Master switch for the "extra usage started" alert (same synced setting as the
 * one in the Notifications section).
 */
function onNotifyOnExtraUsageStartChange(event) {
    store.setNotifyOnExtraUsageStart(event.target.checked)
}

function onUsageFileEnabledChange(provider, event) {
    getProviderHelpers(provider)?.setUsageFileSetting('read_enabled', event.target.checked)
}

function onUsageFilePathInputChange(provider, event) {
    usageFilePathInput.value = { ...usageFilePathInput.value, [provider]: event.target.value }
    // Clear previous validation error when user edits
    if (usageFileValidation.value[provider]) {
        usageFileValidation.value = { ...usageFileValidation.value, [provider]: null }
    }
}

async function onUsageFilePathApply(provider) {
    const helpers = getProviderHelpers(provider)
    if (!helpers) return
    const path = (usageFilePathInput.value[provider] ?? '').trim()
    if (!path) {
        usageFileValidation.value = { ...usageFileValidation.value, [provider]: null }
        helpers.setUsageFileSetting('read_path', '')
        return
    }
    usageFileValidating.value = { ...usageFileValidating.value, [provider]: true }
    usageFileValidation.value = { ...usageFileValidation.value, [provider]: null }
    try {
        const result = await sendValidateUsageFile(provider, path)
        if (result.valid) {
            helpers.setUsageFileSetting('read_path', path)
        } else {
            usageFileValidation.value = { ...usageFileValidation.value, [provider]: result }
        }
    } finally {
        usageFileValidating.value = { ...usageFileValidating.value, [provider]: false }
    }
}

function onUsageDumpEnabledChange(provider, event) {
    getProviderHelpers(provider)?.setUsageFileSetting('dump_enabled', event.target.checked)
}

function onUsageDumpPathInputChange(provider, event) {
    usageDumpPathInput.value = { ...usageDumpPathInput.value, [provider]: event.target.value }
    if (usageDumpValidation.value[provider]) {
        usageDumpValidation.value = { ...usageDumpValidation.value, [provider]: null }
    }
}

async function onUsageDumpPathApply(provider) {
    const helpers = getProviderHelpers(provider)
    if (!helpers) return
    const path = (usageDumpPathInput.value[provider] ?? '').trim()
    if (!path) {
        usageDumpValidation.value = { ...usageDumpValidation.value, [provider]: null }
        helpers.setUsageFileSetting('dump_path', '')
        return
    }
    usageDumpValidating.value = { ...usageDumpValidating.value, [provider]: true }
    usageDumpValidation.value = { ...usageDumpValidation.value, [provider]: null }
    try {
        const result = await sendValidateUsageDumpPath(path)
        if (result.valid) {
            helpers.setUsageFileSetting('dump_path', path)
        } else {
            usageDumpValidation.value = { ...usageDumpValidation.value, [provider]: result }
        }
    } finally {
        usageDumpValidating.value = { ...usageDumpValidating.value, [provider]: false }
    }
}

/**
 * Handle max cached sessions slider change.
 */
function onMaxCachedSessionsChange(event) {
    store.setMaxCachedSessions(event.target.value)
}

/**
 * Toggle auto-unpin on archive.
 */
function onAutoUnpinOnArchiveChange(event) {
    store.setAutoUnpinOnArchive(event.target.checked)
}

function onAllowAgentSessionSharesChange(event) {
    store.setAllowAgentSessionShares(event.target.checked)
}

function onAllowAgentArtifactSharesChange(event) {
    store.setAllowAgentArtifactShares(event.target.checked)
}

/**
 * Toggle title generation.
 */
function onTitleGenerationChange(event) {
    store.setTitleGenerationEnabled(event.target.checked)
}

/**
 * Toggle title auto-apply.
 */
function onTitleAutoApplyChange(event) {
    store.setTitleAutoApply(event.target.checked)
}

/**
 * Handle title system prompt change: update only the local buffer.
 * The store (and backend sync) is only touched when Apply is clicked.
 */
function onTitleSystemPromptChange(event) {
    titleSystemPromptInput.value = event.target.value
}

function onTitleSystemPromptApply() {
    store.setTitleSystemPrompt(titleSystemPromptInput.value)
}

/**
 * Toggle terminal tmux persistence.
 */
function onTmuxChange(event) {
    store.setTerminalUseTmux(event.target.checked)
}

/**
 * Toggle Mac "Option as Meta" in terminals.
 */
function onMacOptionIsMetaChange(event) {
    store.setTerminalMacOptionIsMeta(event.target.checked)
}

/**
 * Toggle copy-on-select in terminals.
 */
function onCopyOnSelectChange(event) {
    store.setTerminalCopyOnSelect(event.target.checked)
}

function onWorktreeDirInputChange(event) {
    worktreeDirInput.value = event.target.value
}

// Commit to the synced setting only when the user hits Apply (mirrors the tmux
// config path control). Trimmed; empty means "no default". A template with an
// unknown placeholder or malformed braces is rejected (no commit).
function onWorktreeDirApply() {
    if (!worktreeTemplateValidation.value.valid) return
    store.setWorktreeDirectoryTemplate(worktreeDirInput.value.trim())
}

function onTmuxConfigPathInputChange(event) {
    tmuxConfigPathInput.value = event.target.value
    if (tmuxConfigValidation.value) tmuxConfigValidation.value = null
}

async function onTmuxConfigPathApply() {
    const path = tmuxConfigPathInput.value.trim()
    if (!path) {
        tmuxConfigValidation.value = null
        store.setTerminalTmuxConfigPath('')
        return
    }
    tmuxConfigValidating.value = true
    tmuxConfigValidation.value = null
    try {
        const result = await sendValidateTmuxConfigPath(path)
        if (result.valid) {
            store.setTerminalTmuxConfigPath(path)
        } else {
            tmuxConfigValidation.value = result
        }
    } finally {
        tmuxConfigValidating.value = false
    }
}

/**
 * Toggle compact session list.
 */
function onCompactSessionListChange(event) {
    store.setCompactSessionList(event.target.checked)
}

/**
 * Toggle per-block message timestamps in the session view.
 */
function onShowMessageTimestampsChange(event) {
    store.setShowMessageTimestamps(event.target.checked)
}

/**
 * Toggle show diffs (auto-expand Edit/Write details).
 */
function onShowDiffsChange(event) {
    store.setShowDiffs(event.target.checked)
}

/**
 * Toggle tool diff word wrap default (for Edit/Write diffs in sessions).
 */
function onToolDiffWordWrapChange(event) {
    store.setToolDiffWordWrap(event.target.checked)
}

/**
 * Toggle tool diff side-by-side default (for Edit/Write diffs in sessions).
 */
function onToolDiffSideBySideChange(event) {
    store.setToolDiffSideBySide(event.target.checked)
}

/**
 * Toggle diff side-by-side default (for the editor/git panel).
 */
function onDiffSideBySideChange(event) {
    store.setDiffSideBySide(event.target.checked)
}

/**
 * Toggle editor word wrap default.
 */
function onEditorWordWrapChange(event) {
    store.setEditorWordWrap(event.target.checked)
}

/**
 * Reset title system prompt to default. Updates the store immediately
 * (per user choice) and resyncs the local buffer so Apply icon goes back
 * to the "synced" state.
 */
function resetTitleSystemPrompt() {
    store.resetTitleSystemPrompt()
    titleSystemPromptInput.value = titleSystemPrompt.value
}

/**
 * Called when the popover itself opens - reset mobile view and refresh
 * notification state.
 *
 * Bound with ``@wa-show.self``: WA ``wa-show`` bubbles, so a nested
 * wa-select / wa-dropdown opening its listbox (e.g. the quota wake-up hour
 * pickers, the theme/brand selects) would otherwise re-fire this handler and
 * slide the mobile view back to the nav while the control stays open. ``.self``
 * restricts it to the popover's own event (target === currentTarget).
 */
function onPopoverShow() {
    mobileShowContent.value = false
    // Seed the worktree-directory template input from the persisted value
    // (General is the default section, so selectSection('general') may not fire
    // on open).
    worktreeDirInput.value = worktreeDirectoryTemplate.value || ''
    seedOriginField('publicBaseUrl')
    seedOriginField('shareBaseUrl')
    if (activeSection.value === 'notifications') {
        nextTick(() => notificationSettingsRef.value?.sync())
    }
}

function openChangelog(options) {
    changelogDialogRef.value?.open(options)
}

function onOpenChangelogEvent() {
    openChangelog({ skipCombined: true })
}
window.addEventListener('open-changelog', onOpenChangelogEvent)
onBeforeUnmount(() => window.removeEventListener('open-changelog', onOpenChangelogEvent))

// Auto-open the changelog on a new version — but hold it back while the startup
// hybrid-mode announcement or the telemetry notice is pending or open (either
// takes priority). Watching all three sources means this re-fires when the last
// holdout closes (hybridAnnouncementActive / telemetryNoticeActive → false) and
// opens the deferred changelog then.
watch(
    [() => dataStore.pendingChangelogVersion, () => dataStore.hybridAnnouncementActive, () => dataStore.telemetryNoticeActive],
    ([version, announcementActive, telemetryNoticeActive]) => {
        if (version && !announcementActive && !telemetryNoticeActive && !forcedChangelogOpen.value) {
            forcedChangelogOpen.value = true
            changelogDialogRef.value?.open()
        }
    },
)

function onChangelogClose() {
    if (forcedChangelogOpen.value) {
        forcedChangelogOpen.value = false
        const version = dataStore.pendingChangelogVersion
        if (version) {
            sendChangelogSeen(version)
        }
        dataStore.clearPendingChangelogVersion()
    }
}
</script>

<template>
    <wa-button id="settings-trigger" variant="neutral" appearance="filled-outlined" size="small">
        <wa-icon name="gear"></wa-icon><span>Settings</span>
        <!-- Last stop for the peer count: see the container query below. -->
        <PeerInboxBadge :count="peersStore.inboxCount" class="settings-trigger-badge" />
    </wa-button>
    <AppTooltip for="settings-trigger">Toggle settings</AppTooltip>
    <wa-popover ref="popoverRef" v-popover-focus-fix for="settings-trigger" placement="top" class="settings-popover" @wa-show.self="onPopoverShow">
        <AppTooltip v-if="showLogout" :for="logoutButtonId">Logout</AppTooltip>
        <div class="settings-layout">
            <div class="settings-layout-inner" :class="{ 'showing-content': mobileShowContent }">
                <!-- Nav: section list -->
                <nav class="settings-nav">
                    <button
                        v-for="section in sections"
                        :key="section.id"
                        class="settings-nav-item"
                        :class="{ active: activeSection === section.id }"
                        @click="selectSection(section.id)"
                    >
                        <ProviderIcon
                            v-if="section.icon"
                            :provider="section.provider"
                            class="settings-nav-provider-icon"
                        />
                        {{ section.navLabel || section.label }}
                        <PeerInboxBadge v-if="section.badge" :count="section.badge" inline />
                        <wa-icon v-if="section.synced" name="cloud" class="synced-icon"></wa-icon>
                    </button>
                    <wa-divider v-if="hasUtilitySections" class="settings-nav-divider"></wa-divider>
                    <button
                        class="settings-nav-item shortcuts-nav-item"
                        :class="{ active: activeSection === 'shortcuts' }"
                        @click="selectSection('shortcuts')"
                    >
                        Shortcuts
                    </button>
                    <button
                        v-if="hasTips"
                        class="settings-nav-item tips-nav-item"
                        :class="{ active: activeSection === 'tips' }"
                        @click="selectSection('tips')"
                    >
                        Tips
                    </button>
                    <button
                        v-if="hasHelp"
                        class="settings-nav-item help-nav-item"
                        :class="{ active: activeSection === 'help' }"
                        @click="selectSection('help')"
                    >
                        Help
                    </button>
                </nav>

                <wa-divider class="settings-vertical-divider" orientation="vertical"></wa-divider>

                <!-- Detail: section content -->
                <div class="settings-detail">
                    <div class="settings-detail-header" @click="goBackToNav">
                        <wa-button
                            variant="neutral"
                            appearance="plain"
                            size="small"
                        >
                            <wa-icon name="arrow-left"></wa-icon>
                        </wa-button>
                        <span class="settings-detail-header-title">
                            {{ activeSectionLabel }}
                            <wa-icon v-if="activeSectionObj?.synced" name="cloud" class="synced-icon"></wa-icon>
                        </span>
                    </div>
                    <div class="settings-sections">

                <!-- General Section -->
                <section v-if="activeSection === 'general'" class="settings-section">
                    <h3 class="settings-section-title">General</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">External address <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <div class="setting-input-apply-row">
                            <wa-input
                                ref="publicBaseUrlInputRef"
                                :value="publicBaseUrlInput"
                                @input="onPublicBaseUrlInputChange"
                                @keydown.enter="onPublicBaseUrlApply"
                                placeholder="https://twicc.example.com"
                                size="small"
                            ></wa-input>
                            <wa-button
                                size="small"
                                variant="neutral"
                                @click="onPublicBaseUrlApply"
                            >
                                <wa-icon :name="publicBaseUrlApplyIcon" slot="start"></wa-icon>
                                Apply
                            </wa-button>
                        </div>
                        <wa-callout v-if="publicBaseUrlError" variant="danger" size="small">{{ publicBaseUrlError }}</wa-callout>
                        <span class="setting-group-hint">
                            Where you reach TwiCC from your devices — used to build links back to
                            your sessions (e.g. in notifications). Leave empty to omit those links.
                            <HelpIconButton help-key="external-url" label="About tunnels &amp; remote access" />
                        </span>
                    </div>
                    <wa-divider></wa-divider>
                    <div class="setting-group">
                        <label class="setting-group-label">Color scheme</label>
                        <wa-select
                            :value.prop="colorScheme"
                            @change="onColorSchemeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in colorSchemeOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Theme <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-select
                            :value.prop="waTheme"
                            @change="onWaThemeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in waThemeOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Accent color <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-select
                            :value.prop="waBrand"
                            @change="onWaBrandChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in waBrandOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Font size ({{fontSize}}px)</label>
                        <wa-slider
                            :min.prop="12"
                            :max.prop="32"
                            :step.prop="1"
                            :value.prop="fontSize"
                            @input="onFontSizeChange"
                            size="small"
                        ></wa-slider>
                    </div>
                    <wa-divider></wa-divider>
                    <div class="setting-group">
                        <label class="setting-group-label">Time display</label>
                        <wa-select
                            :value.prop="sessionTimeFormat"
                            @change="onSessionTimeFormatChange"
                            size="small"
                            class="session-time-format-select"
                        >
                            <wa-option
                                v-for="option in sessionTimeFormatOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Show costs</label>
                        <wa-switch
                            :checked="showCosts"
                            @change="onShowCostsChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                    <wa-divider></wa-divider>
                    <div class="setting-group">
                        <label class="setting-group-label">Worktree directory template <wa-icon name="cloud" class="synced-icon"></wa-icon><HelpIconButton help-key="worktrees" label="What's a worktree?" /></label>
                        <div class="setting-input-apply-row">
                            <wa-input
                                :value="worktreeDirInput"
                                @input="onWorktreeDirInputChange"
                                @keydown.enter="onWorktreeDirApply"
                                placeholder="{git_root}/.worktrees"
                                size="small"
                            ></wa-input>
                            <wa-button
                                size="small"
                                variant="neutral"
                                @click="onWorktreeDirApply"
                                :disabled="!worktreeTemplateValidation.valid"
                            >
                                <wa-icon :name="worktreeDirApplyIcon" slot="start"></wa-icon>
                                Apply
                            </wa-button>
                        </div>
                        <span class="setting-group-hint">
                            Template for the base directory of new git worktrees; pre-fills the path when
                            creating one (<code>../</code> allowed). Placeholders:
                            <code>{git_root}</code> (the project's git root),
                            <code>{project_name}</code> (its name, or its folder name if unnamed),
                            <code>{project_basedir}</code> (its folder name).
                            E.g. <code>{git_root}/.worktrees</code> or <code>/home/me/worktrees/{project_name}</code>.
                            A project can override this with its own absolute directory. Leave empty for no default.
                        </span>
                        <wa-callout
                            v-if="worktreeDirInput.trim() && !worktreeTemplateValidation.valid"
                            variant="danger"
                            size="small"
                            class="usage-file-validation"
                        >{{ worktreeTemplateError }}</wa-callout>
                    </div>
                    <wa-divider></wa-divider>
                    <div class="setting-group">
                        <label class="setting-group-label">Anonymous telemetry <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-switch
                            :checked="telemetryEnabled"
                            @change="onTelemetryEnabledChange"
                            size="small"
                        >Enabled</wa-switch>
                        <span class="setting-group-hint">
                            Anonymous usage statistics — counters only, never content, messages, titles or paths.
                            <a href="https://twicc-telemetry.twidi.com/" target="_blank" rel="noopener">What is collected</a>
                        </span>
                        <div v-if="telemetryEnabled" class="telemetry-actions">
                            <wa-button size="small" appearance="outlined" @click="showTelemetryPayload = true">View last payload</wa-button>
                            <wa-button size="small" appearance="outlined" @click="resetTelemetryInstanceId">Reset instance ID</wa-button>
                        </div>
                    </div>
                </section>

                <!-- Providers section -->
                <section v-if="activeSection === 'providers'" class="settings-section">
                    <h3 class="settings-section-title">Providers <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>
                    <div class="activated-providers-block">
                        <h4>Activated providers</h4>
                        <p class="hint">
                            Disabling a provider stops all of its background tasks, prevents
                            creating new sessions or renaming existing ones, and hides its
                            settings section. Existing sessions remain readable.
                        </p>
                        <div class="provider-switches">
                            <div v-for="p in getRegisteredProviders()" :key="p" class="provider-switch-row">
                                <div class="provider-switch-line">
                                    <wa-switch
                                        class="provider-switch"
                                        :checked="enabledProviders.has(p)"
                                        :disabled="isSwitchDisabled(p)"
                                        @change="(e) => onToggleProvider(p, e)"
                                    >
                                        <ProviderIcon
                                            v-if="providerIconFor(p)"
                                            :provider="p"
                                            class="provider-switch-icon"
                                        />
                                        {{ providerLabelFor(p) }}
                                    </wa-switch>
                                    <template v-if="transitionLabelFor(p)">
                                        <span class="transition-label">{{ transitionLabelFor(p) }}</span>
                                        <wa-spinner class="transition-spinner"></wa-spinner>
                                    </template>
                                </div>
                                <span v-if="reasonFor(p)" class="hint danger">{{ reasonFor(p) }}</span>
                            </div>
                        </div>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default provider for new sessions</label>
                        <wa-select
                            :value.prop="defaultProvider"
                            @change="onDefaultProviderChange"
                            size="small"
                        >
                            <ProviderIcon
                                v-if="providerIconFor(defaultProvider)"
                                slot="start"
                                :provider="defaultProvider"
                            />
                            <wa-option
                                v-for="option in enabledProviderOptions"
                                :key="option.value"
                                :value="option.value"
                                :label="option.label"
                            >
                                <ProviderIcon
                                    v-if="providerIconFor(option.value)"
                                    :provider="option.value"
                                    class="provider-option-icon"
                                />
                                {{ option.label }}
                            </wa-option>
                        </wa-select>
                    </div>
                </section>

                <!-- Per-provider sections — one block per registered provider, identified by its wire key. -->
                <template v-for="section in providerSections" :key="section.id">
                    <ProviderSettingsSection
                        v-if="activeSection === section.id"
                        :provider="section.provider"
                    />
                </template>

                <!-- Notifications Section -->
                <NotificationSettings v-if="activeSection === 'notifications'" ref="notificationSettingsRef" @go-to-public-base-url="goToPublicBaseUrl" />

                <!-- Sharing Section -->
                <section v-if="activeSection === 'sharing'" class="settings-section">
                    <h3 class="settings-section-title">Sharing</h3>
                    <div class="setting-group">
                        <wa-button size="small" appearance="plain" class="sharing-help-link" @click="showHelp('sharing', { showDontShowAgain: false })">
                            <wa-icon name="circle-question" slot="start"></wa-icon>
                            View help
                        </wa-button>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Share host <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <div class="setting-input-apply-row">
                            <wa-input
                                ref="shareBaseUrlInputRef"
                                :value="shareBaseUrlInput"
                                @input="onShareBaseUrlInputChange"
                                @keydown.enter="onShareBaseUrlApply"
                                placeholder="share.example.com"
                                size="small"
                            ></wa-input>
                            <wa-button
                                size="small"
                                variant="neutral"
                                @click="onShareBaseUrlApply"
                            >
                                <wa-icon :name="shareBaseUrlApplyIcon" slot="start"></wa-icon>
                                Apply
                            </wa-button>
                        </div>
                        <wa-callout v-if="shareBaseUrlError" variant="danger" size="small">{{ shareBaseUrlError }}</wa-callout>
                        <span class="setting-group-hint">
                            Dedicated share host — a hostname distinct from this app, pointing at the
                            same port (e.g. a second tunnel hostname). Required to create share links;
                            a different port on the same hostname is not enough. Leave empty to disable sharing.
                        </span>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Agent sharing <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-switch
                            :checked="allowAgentSessionShares"
                            @change="onAllowAgentSessionSharesChange"
                            size="small"
                        >Session shares</wa-switch>
                        <span class="setting-group-hint">
                            Allows agents to create session shares whose target belongs to their own
                            spawn subtree, and to manage session shares created by agents in their own
                            spawn subtree. When enabled, agents can also revoke any existing session
                            share, including links created by you, and read the URL of every existing
                            session share, including links created by you or by another agent.
                        </span>
                        <wa-switch
                            :checked="allowAgentArtifactShares"
                            @change="onAllowAgentArtifactSharesChange"
                            size="small"
                        >Artifact shares</wa-switch>
                        <span class="setting-group-hint">
                            Allows agents to create artifact shares whose target belongs to their own
                            spawn subtree, and to manage artifact shares created by agents in their own
                            spawn subtree. When enabled, agents can also revoke any existing artifact
                            share, including links created by you, and read the URL of every existing
                            artifact share, including links created by you or by another agent.
                        </span>
                    </div>
                    <div class="setting-group">
                        <wa-button size="small" variant="neutral" appearance="accent" @click="showShareManager = true">
                            <wa-icon name="share-nodes" slot="start"></wa-icon>
                            Shared links
                        </wa-button>
                    </div>
                </section>

                <!-- Peers Section -->
                <section v-if="activeSection === 'peers'" class="settings-section">
                    <div class="peer-help-heading">
                        <h3 class="settings-section-title">Peers</h3>
                        <PeerHelpLink />
                    </div>
                    <!-- Once the feature is usable, these are the daily
                         actions and the fields below become set-once
                         configuration — so they lead. Before that the section
                         is a setup form and they lead nowhere: the manager
                         cannot even add a peer without an address. -->
                    <div v-if="hasPeerActions" class="setting-group peer-actions">
                        <wa-button size="small" variant="neutral" appearance="accent" @click="openPeersManager">
                            <wa-icon name="user-group" slot="start"></wa-icon>
                            <span class="peer-action-label">
                                Manage peers
                                <PeerInboxBadge :count="peersStore.pendingRequests.length" inline />
                            </span>
                        </wa-button>
                        <wa-button size="small" variant="neutral" appearance="accent" @click="openPeerInbox">
                            <wa-icon name="envelope" slot="start"></wa-icon>
                            <span class="peer-action-label">
                                Open inbox
                                <PeerInboxBadge :count="peersStore.pendingInboundMessages.length" inline />
                            </span>
                        </wa-button>
                    </div>
                    <!-- Separates the actions from the configuration below;
                         only meaningful when the actions are there. -->
                    <wa-divider v-if="hasPeerActions"></wa-divider>
                    <div class="setting-group">
                        <label class="setting-group-label">Your name <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <div class="setting-input-apply-row">
                            <wa-input
                                :value="peerDisplayNameInput"
                                @input="onPeerDisplayNameInputChange"
                                @keydown.enter="onPeerDisplayNameApply"
                                placeholder="e.g. Stephane (laptop)"
                                size="small"
                            ></wa-input>
                            <wa-button
                                size="small"
                                variant="neutral"
                                @click="onPeerDisplayNameApply"
                            >
                                <wa-icon :name="peerDisplayNameApplyIcon" slot="start"></wa-icon>
                                Apply
                            </wa-button>
                        </div>
                        <span class="setting-group-hint">
                            Shown to peers in your pairing requests so they know who is asking.
                            Empty uses your address's hostname.
                        </span>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Your address <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <div class="setting-input-apply-row">
                            <wa-input
                                ref="peerBaseUrlInputRef"
                                :value="peerBaseUrlInput"
                                @input="onPeerBaseUrlInputChange"
                                @keydown.enter="onPeerBaseUrlApply"
                                placeholder="https://twicc.example.com"
                                size="small"
                            ></wa-input>
                            <wa-button
                                size="small"
                                variant="neutral"
                                @click="onPeerBaseUrlApply"
                            >
                                <wa-icon :name="peerBaseUrlApplyIcon" slot="start"></wa-icon>
                                Apply
                            </wa-button>
                        </div>
                        <wa-callout v-if="peerBaseUrlError" variant="danger" size="small">{{ peerBaseUrlError }}</wa-callout>
                        <wa-callout v-if="peerBaseUrlWarning" variant="warning" size="small">{{ peerBaseUrlWarning }}</wa-callout>
                        <wa-callout v-if="peerBaseUrlConfirmation" variant="warning" size="small">
                            <div class="peer-address-confirmation">
                                <span>
                                    Changing this address disables active Peer relationships and clears their credentials.
                                    You must reconnect each Peer manually.
                                </span>
                                <div class="peer-address-confirmation__actions">
                                    <wa-button size="small" variant="brand" @click="confirmPeerBaseUrlApply">Continue</wa-button>
                                    <wa-button
                                        size="small"
                                        variant="neutral"
                                        appearance="outlined"
                                        @click="cancelPeerBaseUrlApply"
                                    >Cancel</wa-button>
                                </div>
                            </div>
                        </wa-callout>
                        <!-- An action, so a <button> — styled as a link, since
                             that is what reads as clickable in a hint-sized
                             line under a field. -->
                        <button
                            v-if="canPrefillPeerBaseUrl"
                            type="button" class="settings-link-button"
                            @click="prefillPeerBaseUrlFromPublic"
                        >
                            Use the External address from General settings
                        </button>
                        <span class="setting-group-hint">
                            Your address, advertised to peers. Empty disables peer messaging.
                            A different address from External serves peer traffic only; the same
                            address keeps the whole app reachable there.
                            HTTPS strongly recommended. The host must be reachable
                            machine-to-machine: a tunnel-level access gate (e.g. Cloudflare
                            Access asking for an email or Google account) blocks peer calls —
                            use a truly public hostname.
                        </span>
                    </div>
                </section>

                <!-- Sessions Section -->
                <section v-if="activeSection === 'sessions'" class="settings-section">
                    <h3 class="settings-section-title">Sessions</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Display mode</label>
                        <wa-select
                            :value.prop="displayMode"
                            @change="onDisplayModeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in displayModeOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Diffs</label>
                        <wa-switch
                            :checked="showDiffs"
                            @change="onShowDiffsChange"
                            size="small"
                        >Auto open edits</wa-switch>
                        <wa-switch
                            :checked="toolDiffWordWrap"
                            @change="onToolDiffWordWrapChange"
                            size="small"
                        >Word wrap</wa-switch>
                        <wa-switch
                            :checked="toolDiffSideBySide"
                            @change="onToolDiffSideBySideChange"
                            size="small"
                        >Side by side</wa-switch>
                        <span class="setting-group-hint">Inactive if the screen is too narrow.</span>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Message timestamps</label>
                        <wa-switch
                            :checked="showMessageTimestamps"
                            @change="onShowMessageTimestampsChange"
                            size="small"
                        >Show time under each message block</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Auto-unpin on archive <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-switch
                            :checked="autoUnpinOnArchive"
                            @change="onAutoUnpinOnArchiveChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Compact session list</label>
                        <wa-switch
                            :checked="compactSessionList"
                            @change="onCompactSessionListChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Session cache ({{ maxCachedSessions }})</label>
                        <wa-slider
                            :min.prop="1"
                            :max.prop="50"
                            :step.prop="1"
                            :value.prop="maxCachedSessions"
                            @input="onMaxCachedSessionsChange"
                            size="small"
                        ></wa-slider>
                        <span class="setting-group-hint">Number of sessions kept in memory for instant switching.</span>
                    </div>
                </section>

                <!-- Layouts Section -->
                <section v-if="activeSection === 'layouts'" class="settings-section">
                    <h3 class="settings-section-title">Layouts <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Default layout for new sessions</label>
                        <wa-select
                            :value.prop="defaultLayoutId"
                            @change="onDefaultLayoutChange"
                            size="small"
                        >
                            <wa-option
                                v-for="l in selectableLayouts"
                                :key="l.id"
                                :value="l.id"
                                :label="l.name"
                            >{{ l.name }}</wa-option>
                        </wa-select>
                        <span class="setting-group-hint">
                            Save new layouts from a session: dock some panels, then open the layout
                            menu (the <wa-icon name="chevron-down" class="inline-hint-icon"></wa-icon>
                            button at the right of the tab bar) and choose “Save layout”.
                        </span>
                    </div>
                    <div class="setting-group">
                        <wa-button appearance="accent" size="small" @click="onManageLayouts">
                            <wa-icon slot="start" name="sliders"></wa-icon>
                            Manage layouts…
                        </wa-button>
                    </div>
                </section>

                <!-- Title Suggestion Section -->
                <section v-if="activeSection === 'title'" class="settings-section">
                    <h3 class="settings-section-title">Title suggestion <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>
                    <div class="setting-group">
                        <wa-switch
                            :checked="titleGenerationEnabled"
                            @change="onTitleGenerationChange"
                            size="small"
                        >Enabled<template v-if="titleSuggestionModels"> ({{ titleSuggestionModels }})</template></wa-switch>
                        <wa-switch
                            v-if="titleGenerationEnabled"
                            :checked="titleAutoApply"
                            @change="onTitleAutoApplyChange"
                            size="small"
                        >Auto-apply on new sessions</wa-switch>
                        <div v-if="titleGenerationEnabled" class="title-prompt-section">
                            <label class="setting-group-label">System prompt</label>
                            <wa-textarea
                                :value.prop="titleSystemPromptInput"
                                @input="onTitleSystemPromptChange"
                                size="small"
                                rows="7"
                                resize="vertical"
                                class="title-prompt-textarea"
                            ></wa-textarea>
                            <div class="title-prompt-hint">
                                <span>Use <code>{text}</code> as placeholder. Press Apply to save.</span>
                                <div class="title-prompt-actions">
                                    <wa-button
                                        v-if="!isDefaultPrompt"
                                        variant="neutral"
                                        appearance="outlined"
                                        size="small"
                                        @click.stop="resetTitleSystemPrompt"
                                    >Reset to default</wa-button>
                                    <wa-button
                                        size="small"
                                        variant="neutral"
                                        @click.stop="onTitleSystemPromptApply"
                                    >
                                        <wa-icon :name="titleSystemPromptApplyIcon" slot="start"></wa-icon>
                                        Apply
                                    </wa-button>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                <!-- Editor Section -->
                <section v-if="activeSection === 'editor'" class="settings-section">
                    <h3 class="settings-section-title">Editor</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Display</label>
                        <wa-switch
                            :checked="editorWordWrap"
                            @change="onEditorWordWrapChange"
                            size="small"
                        >Word wrap</wa-switch>
                        <wa-switch
                            :checked="diffSideBySide"
                            @change="onDiffSideBySideChange"
                            size="small"
                        >Diff side by side</wa-switch>
                        <span class="setting-group-hint">Inactive if the screen is too narrow.</span>
                    </div>
                </section>

                <!-- Terminal Section -->
                <section v-if="activeSection === 'terminal'" class="settings-section">
                    <h3 class="settings-section-title">Terminal</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Persistent sessions (tmux) <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-switch
                            :checked="terminalUseTmux"
                            @change="onTmuxChange"
                            size="small"
                        >Enabled</wa-switch>
                        <span class="setting-group-hint">Tmux sessions are destroyed when their agent session is archived.</span>
                    </div>
                    <div class="setting-group" v-if="terminalUseTmux">
                        <label class="setting-group-label">Tmux config file <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <div class="usage-file-input-row">
                            <wa-input
                                :value="tmuxConfigPathInput"
                                @input="onTmuxConfigPathInputChange"
                                @keydown.enter="onTmuxConfigPathApply"
                                placeholder="/path/to/tmux.conf (leave empty to ignore)"
                                size="small"
                                :disabled="tmuxConfigValidating"
                            ></wa-input>
                            <wa-button
                                size="small"
                                variant="neutral"
                                @click="onTmuxConfigPathApply"
                                :disabled="tmuxConfigValidating"
                            >
                                <wa-spinner v-if="tmuxConfigValidating" slot="start"></wa-spinner>
                                <wa-icon v-else :name="tmuxConfigApplyIcon" slot="start"></wa-icon>
                                Apply
                            </wa-button>
                        </div>
                        <span class="setting-group-hint">
                            TwiCC always runs tmux on a dedicated socket (<code>-L twicc</code>) and forces
                            <code>mouse off</code> after session creation — these invariants are required for
                            frontend selection and scroll to work. Your config is loaded first (so status bar,
                            colors, bindings apply), then the mouse option is overridden at the session level.
                            Leave empty to ignore any config. Applies to new terminals only.
                        </span>
                        <wa-callout
                            v-if="tmuxConfigValidation && !tmuxConfigValidation.valid"
                            variant="danger"
                            size="small"
                            class="usage-file-validation"
                        >{{ tmuxConfigValidation.message }}</wa-callout>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Copy on select</label>
                        <wa-switch
                            :checked="terminalCopyOnSelect"
                            @change="onCopyOnSelectChange"
                            size="small"
                        >Enabled</wa-switch>
                        <span class="setting-group-hint">
                            When enabled, selecting text in a terminal with the mouse copies it to
                            the clipboard automatically (paste with
                            <kbd>{{ isMac ? '⌘V' : 'Ctrl+V' }}</kbd>) — no need to click Copy. The
                            selection stays visible.
                            <template v-if="isLinux">
                                The text goes to the regular clipboard, <strong>not</strong> the
                                mouse "primary" selection (middle-click paste): browsers can't write
                                to the primary selection, so middle-click won't paste it.
                            </template>
                        </span>
                    </div>
                    <div class="setting-group" v-if="isMac">
                        <label class="setting-group-label">Option key (⌥)</label>
                        <wa-switch
                            :checked="terminalMacOptionIsMeta"
                            @change="onMacOptionIsMetaChange"
                            size="small"
                        >Use as Meta key</wa-switch>
                        <span class="setting-group-hint">
                            When enabled, Option acts as the Meta key for shell shortcuts
                            (<kbd>⌥B</kbd>/<kbd>⌥F</kbd> to move word by word, <kbd>⌥.</kbd> for the last
                            argument), but characters typed with Option — such as <code>|</code>,
                            <code>{</code> or <code>\</code> on international keyboard layouts — can no
                            longer be entered. Stored per device; applies to open terminals immediately.
                        </span>
                    </div>
                </section>

                <!-- Tips Section -->
                <TipsSettings v-if="activeSection === 'tips'" />

                <HelpSettings v-if="activeSection === 'help'" />

                <!-- Providers quotas/usage Section -->
                <section v-if="activeSection === 'usage'" class="settings-section">
                    <h3 class="settings-section-title">Providers quotas/usage</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Show extra usage quota</label>
                        <wa-switch
                            :checked="extraUsageOnlyWhenNeeded"
                            @change="onExtraUsageOnlyWhenNeededChange"
                            size="small"
                        >Only when needed</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">When extra usage starts</label>
                        <wa-switch
                            :checked="notifyOnExtraUsageStart"
                            @change="onNotifyOnExtraUsageStartChange"
                            size="small"
                        >Notify me</wa-switch>
                        <span class="setting-group-hint">
                            Alerts you when a provider starts consuming its extra usage credits again
                            after a quiet period. See the
                            <a href="#" @click.prevent="selectSection('notifications')">Notifications</a>
                            tab for sound, browser and pushed-device options.
                        </span>
                    </div>
                    <template v-for="(provider, idx) in usageProviders" :key="provider">
                        <wa-divider v-if="idx === 0"></wa-divider>
                        <div class="provider-usage-block">
                            <h4 class="provider-usage-title">
                                <ProviderIcon
                                    v-if="providerIconFor(provider)"
                                    :provider="provider"
                                />
                                {{ getProviderLabel(provider) }}
                            </h4>
                        <div v-if="supportsWakeup(provider)" class="setting-group">
                            <label class="setting-group-label">Quota wake-up* <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                            <div class="wakeup-time-row">
                                <wa-select
                                    :value="getWakeupHour(provider)"
                                    @change="onWakeupHourChange(provider, $event)"
                                    size="small"
                                >
                                    <wa-option value="">Off</wa-option>
                                    <wa-option v-for="opt in WAKEUP_HOUR_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</wa-option>
                                </wa-select>
                                <span class="wakeup-time-colon" :class="{ 'is-off': !getWakeupHour(provider) }">:</span>
                                <wa-select
                                    :value="getWakeupMinute(provider)"
                                    @change="onWakeupMinuteChange(provider, $event)"
                                    size="small"
                                    :disabled="!getWakeupHour(provider)"
                                >
                                    <wa-option v-for="m in WAKEUP_MINUTES" :key="m" :value="m">{{ m }}</wa-option>
                                </wa-select>
                            </div>
                        </div>
                        <div class="setting-group">
                            <wa-switch
                                :checked="getReadEnabled(provider)"
                                @change="onUsageFileEnabledChange(provider, $event)"
                                size="small"
                                :disabled="getDumpEnabled(provider)"
                            >Read usage from file** <wa-icon name="cloud" class="synced-icon"></wa-icon></wa-switch>
                            <template v-if="getReadEnabled(provider)">
                                <div class="usage-file-input-row">
                                    <wa-input
                                        :value="usageFilePathInput[provider] ?? ''"
                                        @input="onUsageFilePathInputChange(provider, $event)"
                                        @keydown.enter="onUsageFilePathApply(provider)"
                                        placeholder="/path/to/usage.json"
                                        size="small"
                                        :disabled="!!usageFileValidating[provider]"
                                    ></wa-input>
                                    <wa-button
                                        size="small"
                                        variant="neutral"
                                        @click="onUsageFilePathApply(provider)"
                                        :disabled="!!usageFileValidating[provider]"
                                    >
                                        <wa-spinner v-if="usageFileValidating[provider]" slot="start"></wa-spinner>
                                        <wa-icon v-else :name="readApplyIcon(provider)" slot="start"></wa-icon>
                                        Apply
                                    </wa-button>
                                </div>
                                <span class="setting-group-hint">Press Apply or Enter to validate and save the path.</span>
                                <wa-callout
                                    v-if="usageFileValidation[provider] && !usageFileValidation[provider].valid"
                                    variant="danger"
                                    size="small"
                                    class="usage-file-validation"
                                >{{ usageFileValidation[provider].message }}</wa-callout>
                            </template>
                        </div>
                        <div class="setting-group">
                            <wa-switch
                                :checked="getDumpEnabled(provider)"
                                @change="onUsageDumpEnabledChange(provider, $event)"
                                size="small"
                                :disabled="getReadEnabled(provider)"
                            >Dump usage to file*** <wa-icon name="cloud" class="synced-icon"></wa-icon></wa-switch>
                            <template v-if="getDumpEnabled(provider)">
                                <div class="usage-file-input-row">
                                    <wa-input
                                        :value="usageDumpPathInput[provider] ?? ''"
                                        @input="onUsageDumpPathInputChange(provider, $event)"
                                        @keydown.enter="onUsageDumpPathApply(provider)"
                                        placeholder="/path/to/usage-dump.json"
                                        size="small"
                                        :disabled="!!usageDumpValidating[provider]"
                                    ></wa-input>
                                    <wa-button
                                        size="small"
                                        variant="neutral"
                                        @click="onUsageDumpPathApply(provider)"
                                        :disabled="!!usageDumpValidating[provider]"
                                    >
                                        <wa-spinner v-if="usageDumpValidating[provider]" slot="start"></wa-spinner>
                                        <wa-icon v-else :name="dumpApplyIcon(provider)" slot="start"></wa-icon>
                                        Apply
                                    </wa-button>
                                </div>
                                <span class="setting-group-hint">Press Apply or Enter to validate and save the path.</span>
                                <wa-callout
                                    v-if="usageDumpValidation[provider] && !usageDumpValidation[provider].valid"
                                    variant="danger"
                                    size="small"
                                    class="usage-file-validation"
                                >{{ usageDumpValidation[provider].message }}</wa-callout>
                            </template>
                        </div>
                        </div>
                    </template>
                    <!-- Cross-provider explanations rendered once at the bottom, so each
                         provider block above stays compact (just toggles + path inputs).
                         The synced-icon lives next to each provider's read/dump
                         switches above, where the actual settings are stored. -->
                    <wa-divider></wa-divider>
                    <div class="setting-group usage-mode-explanation">
                        <label class="setting-group-label">* About quota wake-up</label>
                        <span class="setting-group-hint">
                            A provider's 5-hour quota window starts on its first request and resets 5 hours
                            later, so opening it earlier fits more windows into your working day. Pick a time
                            and TwiCC sends a tiny throwaway request at that hour each day to start the window
                            early — skipped if one is already running. It only fires while TwiCC is running at
                            that time (no catch-up if it was off).
                        </span>
                    </div>
                    <div class="setting-group usage-mode-explanation">
                        <label class="setting-group-label">** About read mode</label>
                        <span class="setting-group-hint">
                            If you already maintain a JSON file with usage data outside TwiCC (typically
                            because the provider's API is rate-limited), point to it here and TwiCC will
                            read from this file instead of calling the API directly.
                        </span>
                    </div>
                    <div class="setting-group usage-mode-explanation">
                        <label class="setting-group-label">*** About dump mode</label>
                        <span class="setting-group-hint">
                            Save the raw API response to a JSON file each time TwiCC fetches usage data.
                            Useful if you want to share the data with other tools without extra API calls.
                        </span>
                    </div>
                </section>

                <!-- Keyboard Shortcuts Section -->
                <section v-if="activeSection === 'shortcuts'" class="settings-section shortcuts-section">
                    <h3 class="settings-section-title">Keyboard shortcuts</h3>
                    <div v-for="group in shortcutGroups" :key="group.label" class="shortcut-group">
                        <h4 class="shortcut-group-title">{{ group.label }}</h4>
                        <div class="shortcut-list">
                            <div v-for="(shortcut, i) in group.shortcuts" :key="i" class="shortcut-item">
                                <span class="shortcut-keys">
                                    <template v-for="(key, j) in shortcut.keys" :key="j">
                                        <span v-if="j > 0" class="shortcut-plus">+</span>
                                        <kbd>{{ key }}</kbd>
                                    </template>
                                </span>
                                <span class="shortcut-description">{{ shortcut.description }}</span>
                            </div>
                        </div>
                    </div>
                </section>

                    </div>
                </div>
            </div>
        </div>
        <wa-divider></wa-divider>
        <p class="settings-notice">
            <wa-icon name="cloud" class="synced-icon"></wa-icon>
            Sections and settings marked with a cloud icon are synced across all your devices.
        </p>
        <wa-divider></wa-divider>
        <footer v-if="currentVersion" class="settings-footer">
            <span class="settings-footer-version">
                <a href="https://github.com/twidi/twicc/" target="_blank" rel="noopener">TwiCC v{{ currentVersion }}</a><template v-if="store.isDevMode"> [dev]</template>
                <template v-if="latestVersion">
                    &rarr;
                    <a :href="latestVersion.releaseUrl" target="_blank" rel="noopener">v{{ latestVersion.version }} available</a>
                </template>
            </span>
            ·
            <a href="#" class="settings-footer-changes" @click.prevent="openChangelog()">Changes</a>
            ·
            <a :href="SPONSOR_URL" target="_blank" rel="noopener" class="settings-footer-sponsor">
                <span class="settings-footer-sponsor-icon"></span>
                Sponsor
            </a>
            ·
            <a
                v-if="currentStatusDisplay"
                ref="statusFooterRef"
                :href="currentStatusDisplay.url"
                target="_blank"
                rel="noopener"
                class="settings-footer-status"
                :class="`settings-footer-status--${currentStatusDisplay.modifier}`"
                :id="statusFooterId"
            >
                <ProviderIcon v-if="currentStatusIcon" :provider="currentStatusProvider?.provider" class="settings-footer-status-icon" />
                <span class="status-dot"></span>
                {{ currentStatusDisplay.label }}
            </a>
            <AppTooltip v-if="currentStatusDisplay" :for="statusFooterId">{{ currentStatusDisplay.tooltip }}</AppTooltip>
            <wa-icon
                v-if="currentStatusDisplay && hasMultipleStatusProviders"
                :id="statusNextButtonId"
                class="settings-footer-status-next"
                name="repeat"
                @click="cycleStatusProvider"
            ></wa-icon>
            <AppTooltip v-if="currentStatusDisplay && hasMultipleStatusProviders" :for="statusNextButtonId">Switch to the next provider</AppTooltip>
            <wa-button
                v-if="showLogout"
                :id="logoutButtonId"
                class="logout-button"
                variant="danger"
                appearance="plain"
                size="small"
                @click="handleLogout"
            >
                <wa-icon name="right-from-bracket"></wa-icon>
            </wa-button>
        </footer>
    </wa-popover>
    <ChangelogDialog ref="changelogDialogRef" @close="onChangelogClose" />
    <LayoutManagerDialog ref="layoutManagerDialogRef" />
    <ShareManagerDialog :open="showShareManager" @close="showShareManager = false" />
    <TelemetryPayloadDialog :open="showTelemetryPayload" @close="showTelemetryPayload = false" />
</template>

<style scoped>
#settings-trigger::part(label) {
    display: flex;
    gap: var(--wa-space-s);
}

.settings-popover {
    --max-width: 90vw;
    --arrow-size: 16px;
}

.settings-popover::part(body) {
    padding: 0;
}

/* -- Master-detail layout -- */

.settings-layout {
    display: flex;
    flex-direction: column;
    overflow: hidden;
    height: min(calc(90dvh - 8rem), 50rem);
    width: min(90vw, 700px);
}

.settings-layout-inner {
    display: flex;
    flex: 1;
    min-height: 0;
    width: 100%;
}

/* Nav panel (section list) */

.settings-nav {
    width: 200px;
    min-width: 200px;
    overflow-y: auto;
    padding: var(--wa-space-m);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.settings-nav-item {
    all: unset;
    box-sizing: border-box;
    cursor: pointer;
    padding: var(--wa-space-xs) var(--wa-space-s);
    border-radius: var(--wa-border-radius-m);
    font-size: var(--wa-font-size-m);
    color: var(--wa-color-text);
    text-align: left;
    transition: background 0.15s;
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    wa-icon {
        margin-inline: 0 !important;
    }
}

.settings-nav-item:hover {
    background: var(--wa-color-surface);
}

.settings-nav-item.active {
    color: var(--wa-color-brand);
    font-weight: var(--wa-font-weight-semibold);
}

/* Vertical divider between nav and detail */

.settings-vertical-divider {
    --width: var(--divider-size);
    --spacing: 0;
    align-self: stretch;
    height: auto;
    min-height: 0;
}

/* Detail panel (section content) */

.settings-detail {
    flex: 1;
    min-width: 0;
    overflow-y: auto;
    padding: var(--wa-space-m);
}

/* Detail header (back button) - hidden on desktop */
.settings-detail-header {
    display: none;
}

/* -- Mobile: sliding panels -- */

@media (width < 640px) {
    .settings-layout {
        width: auto;
    }

    .settings-layout-inner {
        width: 200%;
        transition: transform 0.25s ease;
    }

    .settings-layout-inner.showing-content {
        transform: translateX(-50%);
    }

    .settings-nav {
        width: 50%;
        min-width: 50%;
        padding: var(--wa-space-s);
    }

    .settings-vertical-divider {
        display: none;
    }

    .settings-detail {
        width: 50%;
        padding: var(--wa-space-s);
    }

    .settings-detail-header {
        display: flex;
        align-items: center;
        gap: var(--wa-space-2xs);
        cursor: pointer;
        /* Keep the back affordance reachable while the detail panel scrolls:
           stick it to the top of the scrolling panel. The opaque popover
           surface background hides content scrolling underneath; using
           padding-bottom (rather than margin) keeps that masking area opaque
           right down to the content, with no transparent strip. z-index sits
           above the content and the scroll-shadow pseudo-elements (z-index: 2). */
        position: sticky;
        top: 0;
        z-index: 3;
        background: var(--wa-color-surface-default);
        padding-bottom: var(--wa-space-s);
    }

    .settings-detail-header-title {
        font-weight: var(--wa-font-weight-bold);
        font-size: var(--wa-font-size-s);
        color: var(--wa-color-brand);
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }

    .settings-nav-item.active {
        color: var(--wa-color-text);
        font-weight: inherit;
    }

    .settings-nav-item::after {
        content: '›';
        margin-left: auto;
        font-size: 1.3em;
        color: var(--wa-color-text-quiet);
    }
}

/* -- Scroll shadow indicators (progressive enhancement) -- */

@supports (container-type: scroll-state) {
    .settings-nav,
    .settings-detail {
        --_panel-pad: var(--wa-space-m);
        container-type: scroll-state;
    }

    .settings-nav::before,
    .settings-nav::after,
    .settings-detail::before,
    .settings-detail::after {
        --_shadow-color: color-mix(in srgb, var(--wa-color-text-normal) 12%, transparent);
        content: '';
        display: block;
        flex-shrink: 0;
        position: sticky;
        height: 16px;
        margin-inline: calc(-1 * var(--_panel-pad));
        z-index: 2;
        pointer-events: none;
        opacity: 0;
        transition: opacity 0.2s ease;
    }

    .settings-nav::before,
    .settings-detail::before {
        top: 0;
        translate: 0 calc(-1 * var(--_panel-pad));
        background: linear-gradient(to bottom, var(--_shadow-color), transparent);
    }

    .settings-nav::after,
    .settings-detail::after {
        bottom: 0;
        translate: 0 var(--_panel-pad);
        background: linear-gradient(to top, var(--_shadow-color), transparent);
    }

    /* Flex ordering for nav (flex-direction: column) */
    .settings-nav::before {
        order: -1;
    }

    .settings-nav::after {
        order: 9999;
    }

    @container scroll-state(scrollable: top) {
        .settings-nav::before,
        .settings-detail::before {
            opacity: 1;
        }
    }

    @container scroll-state(scrollable: bottom) {
        .settings-nav::after,
        .settings-detail::after {
            opacity: 1;
        }
    }

    @media (width < 640px) {
        .settings-nav,
        .settings-detail {
            --_panel-pad: var(--wa-space-s);
        }
    }
}

/* -- Settings notice (footer bar) -- */

.settings-notice {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    margin: 0;
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-xs) var(--wa-space-s);

    .synced-icon {
        font-size: 1em;
        position: relative;
        top: 0.1em;
        flex-shrink: 0;
    }
}

/* -- Section content styles -- */

.title-prompt-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-xs);
}

.title-prompt-textarea {
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-xs);
}

.title-prompt-hint {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);

    .title-prompt-actions {
        display: flex;
        flex-wrap: wrap;
        gap: var(--wa-space-xs);
        align-items: center;
        justify-content: flex-end;
    }

    code {
        background: var(--wa-color-surface);
        padding: 0 var(--wa-space-2xs);
        border-radius: var(--wa-radius-s);
    }

    wa-button {
        align-self: end;
    }
}

.option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.synced-icon {
    color: var(--wa-color-brand);
}

/* The two peer actions carry their count right after their label. wa-button
   pins any wa-badge slotted STRAIGHT into it to the top corner
   (`.button ::slotted(wa-badge)`), so label and count share one wrapper: the
   badge is then not a slotted child, and the rule no longer matches it. */
.peer-action-label {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

/* -- Nav divider (horizontal, between settings sections and extra items) -- */

.settings-nav-divider {
    --spacing: var(--wa-space-2xs);
}

/* Hide shortcuts entry on touch devices (no keyboard) */
@media (pointer: coarse) {
    .shortcuts-nav-item {
        display: none;
    }
}

/* -- Keyboard shortcuts section -- */

.shortcuts-section {
    gap: var(--wa-space-l) !important;
}

.shortcut-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.shortcut-group-title {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-brand);
    margin: 0;
}

.shortcut-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.shortcut-item {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-m);
    font-size: var(--wa-font-size-s);
    line-height: 1.6;
}

.shortcut-keys {
    display: inline-flex;
    align-items: baseline;
    gap: 2px;
    flex-shrink: 0;
    min-width: 8rem;
}

.shortcut-plus {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-2xs);
    padding: 0 1px;
}

kbd {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 1.4em;
    padding: 0.1em var(--wa-space-2xs);
    font-family: var(--wa-font-family-sans);
    font-size: var(--wa-font-size-xs);
    line-height: 1.4;
    background: var(--wa-color-surface);
    border: 1px solid var(--wa-color-border);
    border-radius: var(--wa-border-radius-s);
    box-shadow: 0 1px 0 var(--wa-color-border);
    white-space: nowrap;
}

.shortcut-description {
    color: var(--wa-color-text);
}

/* -- Footer -- */

wa-popover > wa-divider {
    --width: var(--divider-size);
    --spacing: 0;
}

.settings-footer {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    column-gap: var(--wa-space-xs);
    padding: var(--wa-space-s);
    margin-right: 2rem;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.settings-footer a {
    color: inherit;
    text-decoration: underline;
    text-decoration-style: dotted;
    text-underline-offset: 2px;
}

.settings-footer a:hover {
    color: var(--wa-color-text);
}

.logout-button {
    position: absolute;
    right: 0;
}

.settings-footer-version {
    white-space: nowrap;
}

.settings-footer-status {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    white-space: nowrap;
    text-decoration: none !important;
}

.settings-footer-status:hover {
    text-decoration: underline !important;
}

.settings-footer-status-next {
    cursor: pointer;
    margin-left: var(--wa-space-3xs);
}

.status-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
}

.settings-footer-status--ok .status-dot {
    background-color: var(--wa-color-success);
}

.settings-footer-status--warning .status-dot {
    background-color: var(--wa-color-warning);
}

.settings-footer-status--error .status-dot {
    background-color: var(--wa-color-danger);
}

.settings-footer-status--info .status-dot {
    background-color: var(--wa-color-primary);
}

/* -- Activated providers block -- */

.activated-providers-block {
    margin-bottom: var(--wa-space-l);
}

.activated-providers-block h4 {
    margin: 0 0 var(--wa-space-2xs);
}

.provider-switches {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.provider-switch-row {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--wa-space-2xs);
}

.provider-switch::part(label) {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.provider-switch-icon {
    font-size: 1.2em;
}

.provider-switch-line {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.transition-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-neutral-fill-loud);
    font-style: italic;
}

.transition-spinner {
    font-size: 0.9em;
}

.hint {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-neutral-fill-loud);
}

.hint.danger {
    color: var(--wa-color-danger-fill-loud);
}

</style>

<style>
/* Shared styles for settings sections (used by child components like NotificationSettings) */
.settings-sections .settings-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

/* Horizontal dividers between groups within a section. */
.settings-sections .settings-section wa-divider {
    margin-block: var(--wa-space-s);
}

/* The switch row closing the matrix + weights stack (provider sections): a
   little extra room before the following group. */
.settings-sections .settings-switches {
    margin-bottom: var(--wa-space-s);
}

.settings-sections .settings-section-title {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-bold);
    margin: 0;
    color: var(--wa-color-brand);
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.settings-sections .setting-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    > label ~ :not(label) {
        margin-left: var(--wa-space-s);
        justify-content: flex-start;
        margin-bottom: var(--wa-space-s);
    }
}

/* The "View help" link in the Sharing section hugs its content, pinned to
   the left edge of the section. */
.settings-sections .sharing-help-link {
    align-self: flex-start;
}

/* A native <button> that reads as a link, for an ACTION offered in a hint-sized
   line under a field — a wa-button of any appearance reads as a form control
   there, and stretches to the group's full width. Colours and decoration are
   Web Awesome's own link tokens (native.css `a`), so it matches the real links
   in these sections; the rest resets what native.css gives every <button>
   (form-control height, centring, background).
   Same idea as PeerMessageReviewDialog's `.pr-route__title--link`. */
.settings-sections .settings-link-button {
    align-self: flex-start;
    height: auto;
    min-height: 0;
    padding: 0;
    border: none;
    background: none;
    font: inherit;
    text-align: left;
    cursor: pointer;
    color: var(--wa-color-text-link);
    text-decoration: var(--wa-link-decoration-default);
    text-decoration-thickness: 0.09375em;
    text-underline-offset: 0.125em;
    /* Cancels the bottom margin the group hands every post-label sibling, so
       the link sits just under the field it acts on instead of a gap away. */
    margin-top: calc(-1 * var(--wa-space-s));
}
.settings-sections .settings-link-button:hover {
    color: color-mix(in oklab, var(--wa-color-text-link), var(--wa-color-mix-hover));
    text-decoration: var(--wa-link-decoration-hover);
}

/* The two peer actions read as one row: side by side while they fit, stacked
   when they do not. One `gap` covers both axes, so the wrapped state keeps the
   same spacing as the inline one. Overrides .setting-group's column flow. */
.settings-sections .peer-actions {
    flex-direction: row;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
}

.settings-sections .peer-help-heading {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--wa-space-3xs);
}

.settings-sections .setting-group-label {
    font-size: var(--wa-font-size-m);
    font-weight: var(--wa-font-weight-semibold);
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.settings-sections .setting-group-hint {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}
/* Inline icon inside a hint (e.g. the layout-menu chevron) — keep it on the text baseline. */
.settings-sections .setting-group-hint .inline-hint-icon {
    vertical-align: -0.1em;
}

.usage-file-input-row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
    align-items: center;

    wa-input {
        flex: 1;
    }

    wa-button {
        margin-left: auto;
    }
}

.usage-file-validation {
    margin-top: var(--wa-space-2xs);
}

.wakeup-time-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);

    /* The content (an hour / a minute) is short — keep the selects compact
       and balanced instead of letting them stretch across the row. */
    wa-select {
        flex: 0 0 auto;
        width: 6rem;
    }
}

.wakeup-time-colon {
    font-weight: bold;
    color: var(--wa-color-text-normal);
}

.wakeup-time-colon.is-off {
    color: var(--wa-color-text-quiet);
}

/* Row layout for a text setting paired with an Apply button (worktree template,
   public base URL, …). */
.setting-input-apply-row {
    display: flex;
    gap: var(--wa-space-2xs);
    align-items: center;

    wa-input {
        flex: 1;
    }
}

.peer-address-confirmation {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.peer-address-confirmation__actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs);
}

/* Row of secondary action buttons under the telemetry toggle (view payload,
   reset instance id). */
.telemetry-actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs);
}

.usage-mode-explanation .setting-group-hint {
    /* Mode-explanation paragraphs are read once at the top of the section
       — keep the spacing tight so they read as a header block, not as a
       collection of independent settings. */
    margin-top: 0;
}

.provider-usage-block {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.provider-usage-block + .provider-usage-block {
    margin-top: var(--wa-space-m);
    padding-top: var(--wa-space-m);
    border-top: 1px solid var(--wa-color-surface-border);
}

.provider-usage-title {
    margin: 0;
    font-size: var(--wa-font-size-m);
    color: var(--wa-color-text-normal);
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.provider-option-icon {
    margin-right: 0.5em;
}

@media (width < 640px) {
    .settings-sections .settings-section-title {
        display: none;
    }
}

/* Sidebar footer without Inbox fits, in order of decreasing width:
   1. toggle + command palette + full Settings (with the "Settings" label)
   2. toggle + command palette + Settings compacted to its gear icon  (here)
   3. toggle + Settings (the palette button drops out — see CommandPaletteButton)
   So this "compact Settings" threshold must stay ABOVE the palette button's
   own hide threshold; tune both together. (Only applies inside the `sidebar`
   container, i.e. the footer — not the home screen's fixed Settings button.)
   ProjectView compacts this button earlier while Inbox is visible. */
@container sidebar (width <= 15rem) {
    #settings-trigger {
        &::part(base) {
            padding: var(--wa-space-s);
        }
        & > span {
            display: none;
        }
    }
}

/* Last stop for the peer count. Step 3 of the ladder above leaves Settings as
   the only footer action, so it inherits the badge the Inbox button took with
   it — one click away from the Peers section that details it. The threshold
   MIRRORS PeerInboxButton's own hide threshold: tune the two together, or the
   count is either shown twice or not at all.
   A fully collapsed sidebar is a different case, already covered: the whole
   footer is clipped to a zero-width column and only the toggle survives, so
   ProjectView puts the badge there instead — the two never overlap.
   Inert outside the sidebar (home screen): the container never matches. */
.settings-trigger-badge {
    display: none;
}
@container sidebar (width <= 9rem) {
    .settings-trigger-badge {
        display: inline-flex;
    }
}
</style>
