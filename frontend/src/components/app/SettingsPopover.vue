<script setup>
import { resolveUiMessage } from '../../i18n/uiMessages.js'
import { useI18n } from 'vue-i18n'
import { SUPPORTED_LOCALES } from '../../i18n'
import { interfaceLocaleCompatibility, selectInterfaceLocale } from '../../i18n/androidLocale.js'
import McpSettings from '../mcp/McpSettings.vue'
// SettingsPopover.vue - Settings button with popover panel
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useMediaQuery } from '@vueuse/core'
import { useSettingsStore, SETTINGS_SCHEMA } from '../../stores/settings'
import { useDataStore } from '../../stores/data'
import { useLayoutsStore } from '../../stores/layouts'
import { useAuthStore } from '../../stores/auth'
import { usePeersStore } from '../../stores/peers'
import { useBenchmarkTaskStore } from '../../stores/benchmarkTask'
import { useTextToSpeechStore } from '../../stores/textToSpeech'
import { getProviderHelpers, getProviderLabel, getProviderOptions, getRegisteredProviders, getProviderIcon } from '../../providers'
import ProviderIcon from '../ui/ProviderIcon.vue'
import { getActivationCharMetadata } from '../../utils/commandActivation'
import { validateWorktreeTemplate } from '../../utils/worktreePath'
import { useOriginSettingsForm } from '../../composables/useOriginSettingsForm'
import { usePeerSystemConfigured } from '../../composables/usePeerSystemConfigured'
import {
    DISPLAY_MODE,
    COLOR_SCHEME,
    SESSION_TIME_FORMAT,
    DEFAULT_MAX_CACHED_SESSIONS,
    SPONSOR_URL,
    TITLE_SUGGESTION_MODEL,
    TITLE_SUGGESTION_MODEL_LABELS,
    TITLE_SUGGESTION_MODEL_PROVIDERS,
} from '../../constants'
import NotificationSettings from './NotificationSettings.vue'
import TipsSettings from '../settings/TipsSettings.vue'
import HelpSettings from '../settings/HelpSettings.vue'
import TextToSpeechSettings from '../settings/TextToSpeechSettings.vue'
import SummarySettings from '../settings/SummarySettings.vue'
import HelpIconButton from '../help/HelpIconButton.vue'
import PeerHelpLink from '../peer/PeerHelpLink.vue'
import PeerInboxBadge from '../peer/PeerInboxBadge.vue'
import HelpFeatureLink from '../help/HelpFeatureLink.vue'
import SegmentedControl from '../ui/SegmentedControl.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import BrandLogo from '../ui/BrandLogo.vue'
import ChangelogDialog from './ChangelogDialog.vue'
import LayoutManagerDialog from '../session/layout/LayoutManagerDialog.vue'
import ProviderSettingsSection from './ProviderSettingsSection.vue'
import ShareManagerDialog from '../share/ShareManagerDialog.vue'
import TelemetryPayloadDialog from './TelemetryPayloadDialog.vue'
import { showUpdateToast, sendChangelogSeen, sendValidateUsageDumpPath, sendValidateUsageFile, sendValidateTmuxConfigPath, requestTelemetryInstanceIdReset } from '../../composables/useWebSocket'
import { toast } from '../../composables/useToast'
import { useProviderActivation } from '../../composables/useProviderActivation'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'
import { useGlideInk } from '../../composables/useGlideInk'
import { originFromElement, setNextSchemeOrigin } from '../../utils/colorSchemeTransition'
import {
    SETTINGS_POPOVER_WIDTH_RATIO,
    SETTINGS_POPOVER_MAX_WIDTH,
    SETTINGS_POPOVER_MARGIN,
    resolveSettingsPlacement,
} from '../../utils/settingsPopoverPlacement'

const props = defineProps({
    // Home uses an opaque accent trigger; the icon bar uses a plain trigger.
    triggerAppearance: { type: String, default: 'outlined' },
    triggerLabel: { type: String, default: null },
    triggerIconOnly: { type: Boolean, default: false },
    placement: { type: String, default: 'top' },
    tooltipPlacement: { type: String, default: 'top' },
    positionAnchor: { type: Object, default: null },
})

const { t, locale } = useI18n()
const router = useRouter()
const store = useSettingsStore()
const dataStore = useDataStore()
const layoutsStore = useLayoutsStore()
const authStore = useAuthStore()
const peersStore = usePeersStore()
const benchmarkTaskStore = useBenchmarkTaskStore()
const textToSpeechStore = useTextToSpeechStore()

// Reactive set of currently enabled providers (derived from the settings store).
const enabledProviders = computed(() => new Set(store.enabledProviders))

// Show logout button only when password-based auth is active
const showLogout = computed(() => authStore.passwordRequired && authStore.authenticated)
const logoutButtonId = useId()

async function handleLocaleChange(event) {
    try { await selectInterfaceLocale(event.target.value) }
    catch (error) { toast.error(error.uiMessage || error.message) }
    finally { event.target.value = store.locale }
}

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
            label: t('settings.sections.providerSettings', { provider: label }),
            navLabel: label,
            icon: getProviderIcon(provider),
            synced: true,
            enabled: enabledProviders.value.has(provider),
        }
    })
)

const sections = computed(() => [
    { id: 'general',       label: t('settings.sections.general') },
    { id: 'notifications', label: t('settings.sections.notifications') },
    { id: 'providers',     label: t('settings.sections.providers'), synced: true },
    ...providerSections.value.filter(s => s.enabled),
    { id: 'sessions',      label: t('common.entities.sessions') },
    { id: 'layouts',       label: t('settings.sections.layouts'), synced: true },
    { id: 'title',         label: t('settings.sections.titleSuggestion'), navLabel: t('settings.sections.titles'), synced: true },
    { id: 'editor',        label: t('settings.sections.editor') },
    { id: 'terminal',      label: t('settings.sections.terminal') },
    { id: 'sharing',       label: t('settings.sections.sharing'), synced: true },
    { id: 'usage',         label: t('settings.sections.providerUsage'), navLabel: t('settings.sections.usage') },
    { id: 'summaries',     label: t('settings.sections.summaries'), synced: true },
    { id: 'tts',           label: t('settings.sections.textToSpeech'), synced: true },
    { id: 'mcp',           label: t('settings.sections.externalMcp'), navLabel: t('settings.sections.mcp'), synced: true },
    { id: 'peers',         label: t('settings.sections.peers'), synced: true, badge: peersStore.inboxCount },
])

const activeSection = ref('general')
const mobileShowContent = ref(false)
const popoverRef = ref(null)
let popoverOpen = false
const popoverWidth = computed(() => `${SETTINGS_POPOVER_WIDTH_RATIO * 100}vw`)
const popoverMaxWidth = computed(() => `${SETTINGS_POPOVER_MAX_WIDTH}px`)

async function applyAnchor() {
    const popover = popoverRef.value
    if (!popover) return
    await popover.updateComplete
    if (!popoverRef.value || popoverOpen) return
    const anchor = props.positionAnchor ?? document.getElementById('settings-trigger')
    if (anchor && anchor !== popover.anchor) popover.anchor = anchor
}

onMounted(applyAnchor)
watch(() => props.positionAnchor, applyAnchor)

function onPopoverAfterHide() {
    benchmarkTaskStore.resetTransientControls()
    popoverOpen = false
    applyAnchor()
}

// The chosen section's light fill glides from item to item (visual refresh step 4c).
const navRef = ref(null)
const navInkRef = ref(null)
useGlideInk({
    container: navRef,
    flushTarget: navInkRef,
    getActive: () => navRef.value?.querySelector('.settings-nav-item.active') ?? null,
    // A badge appearing on an item above (the peers inbox count) moves the chosen one.
    getItems: () => [...(navRef.value?.querySelectorAll('.settings-nav-item') ?? [])],
    sources: [activeSection, sections],
})

// The section content crossfades on a section change (visual refresh step 7f): the old one
// fades out, then the new one fades in with a small rise. Desktop only: on a phone the panel
// slide is the only transition, so the swap is instant there (the slide rules' breakpoint).
const isNarrow = useMediaQuery('(width < 640px)')
const detailRef = ref(null)

// The leaving section's DOM stays for its fade: it takes no click and no Tab.
function onSectionLeaving(el) {
    el.inert = true
}

// The next section starts at its top (it is still at opacity 0: the jump is invisible).
function onSectionLeft() {
    if (detailRef.value) detailRef.value.scrollTop = 0
}

// Work that needs the entering section mounted (see goToPublicBaseUrl). One slot, cleared
// before it runs. Keep at most one parameter: a second one makes Vue wait for a done() call.
let afterSwap = null
function onSectionEnter() {
    const run = afterSwap
    afterSwap = null
    if (run) nextTick(run)
}

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
    if (activeSection.value === 'shortcuts') return t('settings.shortcuts.title')
    if (activeSection.value === 'tips') return t('settings.sections.tips')
    if (activeSection.value === 'help') return t('settings.sections.help')
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
            description: t('settings.shortcuts.commandActivation', { command: meta.tooltip, providers: new Intl.ListFormat(locale.value, { style: 'long', type: 'conjunction' }).format(labels) }),
        })
    }

    return [
        {
            label: t('settings.shortcuts.groups.global'),
            shortcuts: [
                { keys: [mod, 'K'], description: t('settings.shortcuts.openPalette') },
                { keys: [mod, 'Shift', 'F'], description: t('settings.shortcuts.openSearch') },
                // Always Ctrl (even on macOS, where ⌘+` is an OS shortcut), so these
                // deliberately don't use `mod`. The cycle key is the physical key
                // above Tab — backquoteKeyLabel resolves its legend per layout, and
                // the description names its position too.
                { keys: ['Ctrl', backquoteKeyLabel.value], description: t('settings.shortcuts.recentSwitcher') },
                { keys: ['Ctrl', 'Shift', backquoteKeyLabel.value], description: t('settings.shortcuts.displayedSwitcher') },
                { keys: ['Alt', 'Shift', 'B'], description: t('settings.shortcuts.toggleSidebar') },
            ]
        },
        {
            label: t('settings.shortcuts.groups.sessionTabs'),
            shortcuts: [
                { keys: ['Alt', 'Shift', '1–9, 0'], description: t('settings.shortcuts.jumpSessionTab') },
                { keys: ['Alt', 'Shift', '←/→'], description: t('settings.shortcuts.previousNextTab') },
                { keys: ['Alt', 'Shift', '↑/↓'], description: t('settings.shortcuts.lastTab') },
            ]
        },
        {
            label: t('settings.shortcuts.groups.sessionChat'),
            shortcuts: [
                { keys: ['Alt', 'Shift', 'M'], description: t('settings.shortcuts.focusInput') },
                { keys: ['Alt', 'Shift', 'PageDown'], description: t('settings.shortcuts.goInput') },
                // PageUp is dual-purpose: pending-request nav always, plus the hybrid
                // CLI terminal only when hybrid mode is enabled — drop that clause off.
                store.isClaudeHybridEnabled
                    ? { keys: ['Alt', 'Shift', 'PageUp'], description: t('settings.shortcuts.goPendingHybrid') }
                    : { keys: ['Alt', 'Shift', 'PageUp'], description: t('settings.shortcuts.goPending') },
                // Hybrid-only chords: listed only when hybrid mode is enabled.
                ...(store.isClaudeHybridEnabled ? [
                    { keys: ['Alt', 'Shift', 'T'], description: t('settings.shortcuts.toggleHybridInput') },
                    { keys: ['Alt', 'Shift', 'H'], description: t('settings.shortcuts.toggleHybrid') },
                ] : []),
                { keys: [t('settings.shortcuts.tripleEscape')], description: t('settings.shortcuts.emergencyStop') },
                { keys: ['Shift', t('settings.shortcuts.tripleEscape')], description: t('settings.shortcuts.hardKill') },
            ]
        },
        {
            label: t('settings.shortcuts.groups.layout'),
            shortcuts: [
                { keys: ['Alt', 'Shift', '↵'], description: t('settings.shortcuts.maximize') },
                { keys: ['Alt', 'Shift', 'Backspace'], description: t('settings.shortcuts.minimize') },
            ]
        },
        {
            label: t('composer.input.label'),
            shortcuts: [
                { keys: [mod, '↵'], description: t('settings.shortcuts.send') },
                { keys: ['Alt', 'Shift', 'O'], description: t('settings.shortcuts.agentSettings') },
                { keys: ['@'], description: t('settings.shortcuts.insertFile') },
                ...commandShortcuts,
                { keys: ['!'], description: t('settings.shortcuts.historyStart') },
                { keys: ['PageUp'], description: t('settings.shortcuts.historyFirstLine') },
            ]
        },
        {
            label: t('settings.shortcuts.groups.pending'),
            shortcuts: [
                { keys: [mod, '↵'], description: t('settings.shortcuts.submitPending') },
            ]
        },
        {
            label: t('settings.shortcuts.groups.projectTabs'),
            shortcuts: [
                { keys: ['Alt', 'Shift', '1–5'], description: t('settings.shortcuts.jumpProjectTab') },
                { keys: ['Alt', 'Shift', '←/→'], description: t('settings.shortcuts.previousNextTab') },
                { keys: ['Alt', 'Shift', '↑/↓'], description: t('settings.shortcuts.lastTab') },
            ]
        },
        {
            label: t('settings.shortcuts.groups.terminalTabs'),
            shortcuts: [
                { keys: ['Alt', 'Ctrl', 'Shift', '1–9'], description: t('settings.shortcuts.jumpTerminalTab') },
                { keys: ['Alt', 'Ctrl', 'Shift', '←/→'], description: t('settings.shortcuts.previousNextTerminal') },
                { keys: ['Alt', 'Ctrl', 'Shift', '↑/↓'], description: t('settings.shortcuts.lastTerminal') },
            ]
        },
        {
            label: t('settings.shortcuts.groups.workflowTabs'),
            shortcuts: [
                { keys: ['Alt', 'Ctrl', 'Shift', '1–9'], description: t('settings.shortcuts.jumpWorkflowTab') },
                { keys: ['Alt', 'Ctrl', 'Shift', '←/→'], description: t('settings.shortcuts.previousNextWorkflow') },
                { keys: ['Alt', 'Ctrl', 'Shift', '↑/↓'], description: t('settings.shortcuts.lastWorkflow') },
            ]
        },
        {
            label: t('settings.shortcuts.groups.search'),
            shortcuts: [
                { keys: [mod, 'F'], description: t('settings.shortcuts.findSession') },
                { keys: ['F3'], description: t('settings.shortcuts.nextMatch') },
                { keys: ['Shift', 'F3'], description: t('settings.shortcuts.previousMatch') },
            ]
        },
        {
            label: t('settings.shortcuts.groups.editor'),
            shortcuts: [
                { keys: ['Alt', 'E'], description: t('settings.shortcuts.toggleEdit') },
            ]
        },
        {
            label: t('settings.sections.terminal'),
            shortcuts: [
                { keys: ['Ctrl', 'C'], description: t('settings.shortcuts.copyInsteadSignal') },
                { keys: ['Ctrl', 'Shift', 'C'], description: t('settings.shortcuts.copy') },
                { keys: ['Ctrl', 'D'], description: t('settings.shortcuts.eof') },
            ]
        },
    ]
})

// Color scheme options for the select
const colorSchemeOptions = computed(() => [
    { value: COLOR_SCHEME.SYSTEM, label: t('settings.general.colorScheme.system') },
    { value: COLOR_SCHEME.LIGHT, label: t('settings.general.colorScheme.light') },
    { value: COLOR_SCHEME.DARK, label: t('settings.general.colorScheme.dark') },
])

// Session time format options for the select
const sessionTimeFormatOptions = computed(() => [
    { value: SESSION_TIME_FORMAT.TIME, label: t('settings.general.timeDisplay.absolute') },
    { value: SESSION_TIME_FORMAT.RELATIVE_SHORT, label: t('settings.general.timeDisplay.relativeShort') },
    { value: SESSION_TIME_FORMAT.RELATIVE_NARROW, label: t('settings.general.timeDisplay.relativeNarrow') },
])

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
    if (state === 'starting') return t('session.process.starting')
    if (state === 'stopping') return t('session.process.stopping')
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
const hideFiveHourUsage = computed(() => store.isFiveHourUsageHidden)
const maxCachedSessions = computed(() => store.getMaxCachedSessions)
const autoUnpinOnArchive = computed(() => store.isAutoUnpinOnArchive)
const allowAgentSessionShares = computed(() => store.isAllowAgentSessionShares)
const allowAgentArtifactShares = computed(() => store.isAllowAgentArtifactShares)
const titleGenerationEnabled = computed(() => store.isTitleGenerationEnabled)
const titleAutoApply = computed(() => store.isTitleAutoApply)
// The radio shows the EFFECTIVE model, not the stored one: a forced choice
// whose provider is disabled, or an OpenRouter choice without its key,
// displays as the model that will really run. The stored value is left
// untouched, so enabling the provider again (or setting the key and restarting
// TwiCC) restores the user's choice by itself (see
// ``resolveEffectiveTitleSuggestionModel``).
const titleSuggestionModel = computed(() => store.getEffectiveTitleSuggestionModel)
// Whether ``TWICC_OPENROUTER_API_KEY`` is set (never its value): OpenRouter needs it.
const openrouterKeySet = computed(() => textToSpeechStore.keyPresent)

/**
 * The forced title models that cannot run, mapped to the reason shown under
 * the radio: an agent model whose provider is disabled, OpenRouter without
 * its key. A model in this map is not selectable.
 */
const disabledTitleSuggestionModels = computed(() => {
    const disabled = {}
    for (const [model, provider] of Object.entries(TITLE_SUGGESTION_MODEL_PROVIDERS)) {
        if (enabledProviders.value.has(provider)) continue
        disabled[model] = t('settings.titles.model.providerDisabled', { provider: getProviderLabel(provider) })
    }
    // The help page tells how to set the key.
    if (!openrouterKeySet.value) {
        disabled[TITLE_SUGGESTION_MODEL.OPENROUTER] = t('settings.titles.model.keyMissing')
    }
    return disabled
})

/** The Titles segments: the model labels, a model that cannot run shown disabled with the reason as tooltip. */
const titleModelOptions = computed(() => [
    { value: TITLE_SUGGESTION_MODEL.PROVIDER, label: t('settings.titles.model.followProvider') },
    ...[TITLE_SUGGESTION_MODEL.HAIKU, TITLE_SUGGESTION_MODEL.LUNA, TITLE_SUGGESTION_MODEL.OPENROUTER].map(model => ({
        value: model,
        label: TITLE_SUGGESTION_MODEL_LABELS[model],
        ...(disabledTitleSuggestionModels.value[model]
            ? { disabled: true, title: `${TITLE_SUGGESTION_MODEL_LABELS[model]}: ${disabledTitleSuggestionModels.value[model]}` }
            : {}),
    })),
].map(option => titleGenerationEnabled.value ? option : { ...option, disabled: true }))

// The user's stored choice is not the one running: a disabled provider, or a
// missing OpenRouter key, moved the selection. Gates the sentence promising
// the choice comes back.
const titleSuggestionModelIsDisplaced = computed(
    () => store.getTitleSuggestionModel !== store.getEffectiveTitleSuggestionModel,
)
// The displaced choice is OpenRouter: it comes back with the key, not with a provider.
const displacedTitleModelIsOpenRouter = computed(
    () => titleSuggestionModelIsDisplaced.value && store.getTitleSuggestionModel === TITLE_SUGGESTION_MODEL.OPENROUTER,
)
const titleSystemPrompt = computed(() => store.getTitleSystemPrompt)
const titleSystemPromptInput = ref('')
const terminalUseTmux = computed(() => store.isTerminalUseTmux)
const terminalTmuxConfigPath = computed(() => store.getTerminalTmuxConfigPath)
const terminalMacOptionIsMeta = computed(() => store.isTerminalMacOptionIsMeta)
const terminalCopyOnSelect = computed(() => store.isTerminalCopyOnSelect)
const isMac = computed(() => store.isMac)
const isLinux = computed(() => store.isLinux)
const worktreeDirectoryTemplate = computed(() => store.getWorktreeDirectoryTemplate)
const compactSessionList = computed(() => store.isCompactSessionList)
const showSessionsAsTree = computed(() => store.isShowSessionsAsTree)
const showProviderIcons = computed(() => store.isShowProviderIcons)
const showActiveAcrossFilters = computed(() => store.isShowActiveAcrossFilters)
const sidebarRailVisibleWhenClosed = computed(() => store.isSidebarRailVisibleWhenClosed)
const reduceEffects = computed(() => store.isReduceEffects)
const showMessageTimestamps = computed(() => store.areMessageTimestampsShown)
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
// other clocks). Rebuild when the selected interface locale changes.
const WAKEUP_HOUR_OPTIONS = computed(() => {
    const fmt = new Intl.DateTimeFormat(locale.value, { hour: 'numeric' })
    return Array.from({ length: 24 }, (_, h) => ({
        value: String(h).padStart(2, '0'),
        label: fmt.format(new Date(2000, 0, 1, h)),
    }))
})
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
        const list = new Intl.ListFormat(locale.value, { style: 'long', type: 'conjunction' }).format(v.unknown.map(name => `{${name}}`))
        return t('settings.general.worktreeDirectory.errors.unknown', { placeholders: list, allowed: '{git_root}, {project_name}, {project_basedir}' }, v.unknown.length)
    }
    return t('settings.general.worktreeDirectory.errors.malformed', { openBrace: '{', closeBrace: '}' })
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
// whether the icon bar inbox button exists. See `usePeerSystemConfigured`.
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
const displayModeOptions = computed(() => [
    { value: DISPLAY_MODE.CONVERSATION, label: t('settings.sessions.displayMode.conversation') },
    { value: DISPLAY_MODE.SIMPLIFIED, label: t('settings.sessions.displayMode.simplified') },
    { value: DISPLAY_MODE.NORMAL, label: t('settings.sessions.displayMode.detailed') },
])


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

// The new scheme grows in a circle from the select (step 5c).
function onColorSchemeChange(event) {
    if (event.target.value !== store.colorScheme) {
        setNextSchemeOrigin(originFromElement(event.target))
        store.setColorScheme(event.target.value)
    }
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
// focus the External address field. After a section swap the field only exists once General
// enters (after the exit fade), so the focus waits for the Transition's enter hook.
function goToPublicBaseUrl() {
    const swaps = activeSection.value !== 'general'
    selectSection('general')
    const focusInput = () => publicBaseUrlInputRef.value?.focus()
    if (swaps) afterSwap = focusInput
    else nextTick(focusInput)
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
        toast.success(t('settings.general.telemetry.resetSuccess'))
    } else {
        toast.error(t('settings.general.telemetry.resetError'))
    }
}

/**
 * Toggle extra usage "only when needed" mode.
 */
function onExtraUsageOnlyWhenNeededChange(event) {
    store.setExtraUsageOnlyWhenNeeded(event.target.checked)
}

/**
 * Toggle the 5-hour usage quota display.
 */
function onHideFiveHourUsageChange(event) {
    store.setHideFiveHourUsage(event.target.checked)
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
 * Select the model used for title suggestions.
 */
function onTitleSuggestionModelChange(event) {
    store.setTitleSuggestionModel(event.target.value)
}

/**
 * Adopt the clicked option, even when it is the one already displayed.
 *
 * A displaced selection (stored model whose provider is disabled) shows the
 * fallback as checked, so clicking that fallback changes nothing and
 * ``wa-radio-group`` emits no ``change`` — the user would have no way to make
 * it their stored choice. Writing it here covers that click. Mirrors the
 * group's own guards: a disabled radio, or a disabled group, is ignored.
 */
function onTitleSuggestionModelClick(event) {
    if (!titleGenerationEnabled.value) return
    const radio = event.target?.closest?.('wa-radio')
    if (!radio || radio.disabled) return
    store.setTitleSuggestionModel(radio.value)
}

/**
 * The keyboard counterpart of ``onTitleSuggestionModelClick``.
 *
 * Space re-selects the focused radio, which Web Awesome turns into no event
 * when it is already the checked one — the same dead end the click handler
 * covers. Arrow keys always move the value, so they go through ``change``.
 */
function onTitleSuggestionModelKeydown(event) {
    if (event.key !== ' ') return
    onTitleSuggestionModelClick(event)
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
function onReduceEffectsChange(event) {
    store.setReduceEffects(event.target.checked)
}

function onSidebarRailVisibleWhenClosedChange(event) {
    store.setSidebarRailVisibleWhenClosed(event.target.checked)
}

function onCompactSessionListChange(event) {
    store.setCompactSessionList(event.target.checked)
}

function onShowSessionsAsTreeChange(event) {
    store.setShowSessionsAsTree(event.target.checked)
}

function onShowProviderIconsChange(event) {
    store.setShowProviderIcons(event.target.checked)
}

function onShowActiveAcrossFiltersChange(event) {
    store.setShowActiveAcrossFilters(event.target.checked)
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
 * pickers, the color scheme select) would otherwise re-fire this handler and
 * slide the mobile view back to the nav while the control stays open. ``.self``
 * restricts it to the popover's own event (target === currentTarget).
 */
function onPopoverShow() {
    popoverOpen = true
    const popover = popoverRef.value
    // Bound the whole body, including the wrapped footer, to the anchor's available space.
    if (popover?.popup) {
        popover.popup.autoSize = 'vertical'
        popover.popup.autoSizePadding = SETTINGS_POPOVER_MARGIN
        popover.popup.shiftPadding = SETTINGS_POPOVER_MARGIN
    }
    if (props.placement !== 'top' && popover?.anchor) {
        popover.placement = resolveSettingsPlacement({
            preferred: props.placement,
            anchorRight: popover.anchor.getBoundingClientRect().right,
            innerWidth: window.innerWidth,
        })
    }
    // The provider sections' model × effort matrix starts with older models
    // hidden and auto-select off, whatever the last opening (or the session
    // popover) left behind.
    benchmarkTaskStore.resetTransientControls()
    mobileShowContent.value = false
    // A pending post-swap focus whose swap never ran must not fire later.
    afterSwap = null
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

function onShowUpdateInstructions() {
    popoverRef.value?.hide()
    showUpdateToast(latestVersion.value?.version)
}

function onOpenChangelogEvent() {
    openChangelog({ availableUpdates: true })
}
window.addEventListener('open-changelog', onOpenChangelogEvent)
onBeforeUnmount(() => window.removeEventListener('open-changelog', onOpenChangelogEvent))

// Auto-open the changelog on a new version — but hold it back while the
// provider-activation dialog, the startup hybrid-mode announcement or the
// telemetry notice is pending or open (each takes priority). Watching every
// source means this re-fires when the last holdout closes (its flag → false)
// and opens the deferred changelog then.
watch(
    [
        () => dataStore.pendingChangelogVersion,
        () => dataStore.hybridAnnouncementActive,
        () => dataStore.telemetryNoticeActive,
        () => dataStore.providerActivationActive,
    ],
    ([version, announcementActive, telemetryNoticeActive, providerActivationActive]) => {
        if (
            version && !announcementActive && !telemetryNoticeActive
            && !providerActivationActive && !forcedChangelogOpen.value
        ) {
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
    <wa-button id="settings-trigger" :class="{ 'settings-trigger--icon-only': props.triggerIconOnly }" variant="brand" :appearance="props.triggerAppearance" size="small">
        <wa-icon name="gear"></wa-icon><span class="settings-trigger-label">{{ props.triggerLabel ?? $t('settings.trigger.label') }}</span>
    </wa-button>
    <AppTooltip for="settings-trigger" :placement="props.tooltipPlacement">{{ props.triggerLabel ?? $t('settings.trigger.tooltip') }}</AppTooltip>
    <Teleport to="body">
        <wa-popover ref="popoverRef" v-popover-focus-fix for="settings-trigger" :placement="props.placement" class="settings-popover" @wa-show.self="onPopoverShow" @wa-after-hide.self="onPopoverAfterHide">
            <div class="settings-layout">
                <div class="settings-layout-inner" :class="{ 'showing-content': mobileShowContent }">
                    <!-- Nav: section list -->
                    <nav ref="navRef" v-scroll-shadow class="settings-nav">
                        <span ref="navInkRef" class="glide-ink settings-nav-ink" aria-hidden="true"></span>
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
                        <!-- Shortcuts is always present, so the divider is too. A touch device can
                             have a keyboard plugged in at any moment (and unplugged the next), so
                             the cheat sheet stays reachable everywhere rather than tracking a flag
                             that would be stale as soon as it is read. -->
                        <wa-divider class="settings-nav-divider"></wa-divider>
                        <button
                            class="settings-nav-item shortcuts-nav-item"
                            :class="{ active: activeSection === 'shortcuts' }"
                            @click="selectSection('shortcuts')"
                        >
                            {{ $t('settings.sections.shortcuts') }}
                        </button>
                        <button
                            class="settings-nav-item tips-nav-item"
                            :class="{ active: activeSection === 'tips' }"
                            @click="selectSection('tips')"
                        >
                            {{ $t('settings.sections.tips') }}
                        </button>
                        <button
                            class="settings-nav-item help-nav-item"
                            :class="{ active: activeSection === 'help' }"
                            @click="selectSection('help')"
                        >
                            {{ $t('settings.sections.help') }}
                        </button>
                    </nav>

                    <wa-divider class="settings-vertical-divider" orientation="vertical"></wa-divider>

                    <!-- Detail: section content -->
                    <div ref="detailRef" v-scroll-shadow class="settings-detail">
                        <div class="settings-detail-header glass-sticky" @click="goBackToNav">
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
                        <!-- Section crossfade (step 7f): one keyed wrapper, desktop only (see isNarrow). -->
                        <Transition
                            name="settings-swap"
                            :mode="isNarrow ? undefined : 'out-in'"
                            :css="!isNarrow"
                            @before-leave="onSectionLeaving"
                            @enter="onSectionEnter"
                            @after-leave="onSectionLeft"
                        >
                        <div :key="activeSection" class="settings-swap">

                    <!-- General Section -->
                    <section v-if="activeSection === 'general'" class="settings-section">
                        <h3 class="settings-section-title">{{ $t('settings.sections.general') }}</h3>
                        <div class="setting-group">
                            <wa-select
                                :label="$t('settings.general.language.label')"
                                :value.prop="store.locale"
                                @change="handleLocaleChange"
                                size="small"
                            >
                                <wa-option v-for="locale in SUPPORTED_LOCALES" :key="locale.value" :value="locale.value">
                                    {{ locale.label }}
                                </wa-option>
                            </wa-select>
                            <span class="setting-group-hint">{{ $t('settings.general.language.hint') }}</span>
                            <wa-callout v-if="interfaceLocaleCompatibility" variant="warning">
                                {{ resolveUiMessage(interfaceLocaleCompatibility) }}
                            </wa-callout>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.general.externalAddress.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                            <div class="setting-input-apply-row">
                                <!-- eslint-disable @intlify/vue-i18n/no-raw-text -- This placeholder is an example URL. -->
                                <wa-input
                                    ref="publicBaseUrlInputRef"
                                    :value="publicBaseUrlInput"
                                    @input="onPublicBaseUrlInputChange"
                                    @keydown.enter="onPublicBaseUrlApply"
                                    placeholder="https://twicc.example.com"
                                    size="small"
                                ></wa-input>
                                <!-- eslint-enable @intlify/vue-i18n/no-raw-text -->
                                <wa-button
                                    size="small"
                                    variant="neutral"
                                    @click="onPublicBaseUrlApply"
                                >
                                    <wa-icon :name="publicBaseUrlApplyIcon" slot="start"></wa-icon>
                                    {{ $t('common.actions.apply') }}
                                </wa-button>
                            </div>
                            <wa-callout v-if="publicBaseUrlError" variant="danger" size="small">{{ publicBaseUrlError }}</wa-callout>
                            <span class="setting-group-hint">
                                {{ $t('settings.general.externalAddress.hint') }}
                                <HelpIconButton help-key="external-url" :label="$t('help.externalAccess')" />
                            </span>
                        </div>
                        <wa-divider></wa-divider>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.general.reduceEffects.label') }}</label>
                            <wa-switch
                                :checked="reduceEffects"
                                @change="onReduceEffectsChange"
                                size="small"
                            >{{ $t('common.status.enabled') }}</wa-switch>
                            <span class="setting-group-hint">{{ $t('settings.general.reduceEffects.hint') }}</span>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.general.colorScheme.label') }}</label>
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
                            <label class="setting-group-label">{{ $t('settings.general.fontSize.label', { size: fontSize }) }}</label>
                            <wa-slider
                                :min.prop="12"
                                :max.prop="32"
                                :step.prop="1"
                                :value.prop="fontSize"
                                @input="onFontSizeChange"
                                size="small"
                            ></wa-slider>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.general.keepRailVisible.label') }}</label>
                            <wa-switch
                                :checked="sidebarRailVisibleWhenClosed"
                                @change="onSidebarRailVisibleWhenClosedChange"
                                size="small"
                            >{{ $t('common.status.enabled') }}</wa-switch>
                        </div>
                        <wa-divider></wa-divider>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.general.timeDisplay.label') }}</label>
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
                            <label class="setting-group-label">{{ $t('settings.general.showCosts.label') }}</label>
                            <wa-switch
                                :checked="showCosts"
                                @change="onShowCostsChange"
                                size="small"
                            >{{ $t('common.status.enabled') }}</wa-switch>
                        </div>
                        <wa-divider></wa-divider>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.general.worktreeDirectory.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon><HelpIconButton help-key="worktrees" :label="$t('help.worktrees')" /></label>
                            <div class="setting-input-apply-row">
                                <!-- eslint-disable @intlify/vue-i18n/no-raw-text -- This placeholder preserves the worktree template syntax. -->
                                <wa-input
                                    :value="worktreeDirInput"
                                    @input="onWorktreeDirInputChange"
                                    @keydown.enter="onWorktreeDirApply"
                                    placeholder="{git_root}/.worktrees"
                                    size="small"
                                ></wa-input>
                                <!-- eslint-enable @intlify/vue-i18n/no-raw-text -->
                                <wa-button
                                    size="small"
                                    variant="neutral"
                                    @click="onWorktreeDirApply"
                                    :disabled="!worktreeTemplateValidation.valid"
                                >
                                    <wa-icon :name="worktreeDirApplyIcon" slot="start"></wa-icon>
                                    {{ $t('common.actions.apply') }}
                                </wa-button>
                            </div>
                            <i18n-t keypath="settings.general.worktreeDirectory.hint" scope="global" tag="span" class="setting-group-hint">
                                <template #relative><code>../</code></template>
                                <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- This is a literal worktree template placeholder. -->
                                <template #gitRoot><code>{git_root}</code></template>
                                <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- This is a literal worktree template placeholder. -->
                                <template #projectName><code>{project_name}</code></template>
                                <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- This is a literal worktree template placeholder. -->
                                <template #projectBasedir><code>{project_basedir}</code></template>
                                <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- This example preserves the worktree template syntax. -->
                                <template #exampleRelative><code>{git_root}/.worktrees</code></template>
                                <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- This example preserves a path and worktree template syntax. -->
                                <template #exampleAbsolute><code>/home/me/worktrees/{project_name}</code></template>
                            </i18n-t>
                            <wa-callout
                                v-if="worktreeDirInput.trim() && !worktreeTemplateValidation.valid"
                                variant="danger"
                                size="small"
                                class="usage-file-validation"
                            >{{ worktreeTemplateError }}</wa-callout>
                        </div>
                        <wa-divider></wa-divider>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.general.telemetry.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                            <wa-switch
                                :checked="telemetryEnabled"
                                @change="onTelemetryEnabledChange"
                                size="small"
                            >{{ $t('common.status.enabled') }}</wa-switch>
                            <span class="setting-group-hint">
                                {{ $t('settings.general.telemetry.hint') }}
                                <a href="https://twicc-telemetry.twidi.com/" target="_blank" rel="noopener">{{ $t('settings.general.telemetry.collectionLink') }}</a>
                            </span>
                            <div v-if="telemetryEnabled" class="telemetry-actions">
                                <wa-button size="small" appearance="outlined" @click="showTelemetryPayload = true">{{ $t('settings.general.telemetry.viewPayload') }}</wa-button>
                                <wa-button size="small" appearance="outlined" @click="resetTelemetryInstanceId">{{ $t('settings.general.telemetry.resetInstanceId') }}</wa-button>
                            </div>
                        </div>
                    </section>

                    <!-- Providers section -->
                    <section v-if="activeSection === 'providers'" class="settings-section">
                        <h3 class="settings-section-title">{{ $t('settings.sections.providers') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>
                        <div class="activated-providers-block">
                            <h4>{{ $t('settings.providers.enabled.label') }}</h4>
                            <p class="hint">
                                {{ $t('settings.providers.enabled.hint') }}
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
                            <label class="setting-group-label">{{ $t('settings.providers.defaultProvider.label') }}</label>
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

                    <SummarySettings v-if="activeSection === 'summaries'" />
                    <TextToSpeechSettings v-if="activeSection === 'tts'" />

                    <McpSettings v-if="activeSection === 'mcp'" />
                    <!-- Sharing Section -->
                    <section v-if="activeSection === 'sharing'" class="settings-section">
                        <div class="settings-section-heading">
                            <h3 class="settings-section-title">{{ $t('settings.sections.sharing') }}</h3>
                            <HelpFeatureLink help-key="sharing" :label="$t('help.sharing')" />
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.sharing.host.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                            <div class="setting-input-apply-row">
                                <!-- eslint-disable @intlify/vue-i18n/no-raw-text -- This placeholder is an example share hostname. -->
                                <wa-input
                                    ref="shareBaseUrlInputRef"
                                    :value="shareBaseUrlInput"
                                    @input="onShareBaseUrlInputChange"
                                    @keydown.enter="onShareBaseUrlApply"
                                    placeholder="share.example.com"
                                    size="small"
                                ></wa-input>
                                <!-- eslint-enable @intlify/vue-i18n/no-raw-text -->
                                <wa-button
                                    size="small"
                                    variant="neutral"
                                    @click="onShareBaseUrlApply"
                                >
                                    <wa-icon :name="shareBaseUrlApplyIcon" slot="start"></wa-icon>
                                    {{ $t('common.actions.apply') }}
                                </wa-button>
                            </div>
                            <wa-callout v-if="shareBaseUrlError" variant="danger" size="small">{{ shareBaseUrlError }}</wa-callout>
                            <span class="setting-group-hint">
                                {{ $t('settings.sharing.host.hint') }}
                            </span>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.sharing.agentPermissions.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                            <wa-switch
                                :checked="allowAgentSessionShares"
                                @change="onAllowAgentSessionSharesChange"
                                size="small"
                            >{{ $t('settings.sharing.agentPermissions.sessionShares') }}</wa-switch>
                            <span class="setting-group-hint">
                                {{ $t('settings.sharing.agentPermissions.sessionSharesHint') }}
                            </span>
                            <wa-switch
                                :checked="allowAgentArtifactShares"
                                @change="onAllowAgentArtifactSharesChange"
                                size="small"
                            >{{ $t('settings.sharing.agentPermissions.artifactShares') }}</wa-switch>
                            <span class="setting-group-hint">
                                {{ $t('settings.sharing.agentPermissions.artifactSharesHint') }}
                            </span>
                        </div>
                        <div class="setting-group">
                            <wa-button size="small" variant="neutral" appearance="accent" @click="showShareManager = true">
                                <wa-icon name="share-nodes" slot="start"></wa-icon>
                                {{ $t('settings.sharing.manageLinks') }}
                            </wa-button>
                        </div>
                    </section>

                    <!-- Peers Section -->
                    <section v-if="activeSection === 'peers'" class="settings-section">
                        <div class="settings-section-heading">
                            <h3 class="settings-section-title">{{ $t('settings.sections.peers') }}</h3>
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
                                    {{ $t('settings.peers.manage') }}
                                    <PeerInboxBadge :count="peersStore.pendingRequests.length" inline />
                                </span>
                            </wa-button>
                            <wa-button size="small" variant="neutral" appearance="accent" @click="openPeerInbox">
                                <wa-icon name="envelope" slot="start"></wa-icon>
                                <span class="peer-action-label">
                                    {{ $t('settings.peers.openInbox') }}
                                    <PeerInboxBadge :count="peersStore.pendingInboundMessages.length" inline />
                                </span>
                            </wa-button>
                        </div>
                        <!-- Separates the actions from the configuration below;
                             only meaningful when the actions are there. -->
                        <wa-divider v-if="hasPeerActions"></wa-divider>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.peers.name.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                            <div class="setting-input-apply-row">
                                <wa-input
                                    :value="peerDisplayNameInput"
                                    @input="onPeerDisplayNameInputChange"
                                    @keydown.enter="onPeerDisplayNameApply"
                                    :placeholder="$t('settings.peers.name.placeholder')"
                                    size="small"
                                ></wa-input>
                                <wa-button
                                    size="small"
                                    variant="neutral"
                                    @click="onPeerDisplayNameApply"
                                >
                                    <wa-icon :name="peerDisplayNameApplyIcon" slot="start"></wa-icon>
                                    {{ $t('common.actions.apply') }}
                                </wa-button>
                            </div>
                            <span class="setting-group-hint">
                                {{ $t('settings.peers.name.hint') }}
                            </span>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.peers.address.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                            <div class="setting-input-apply-row">
                                <!-- eslint-disable @intlify/vue-i18n/no-raw-text -- This placeholder is an example URL. -->
                                <wa-input
                                    ref="peerBaseUrlInputRef"
                                    :value="peerBaseUrlInput"
                                    @input="onPeerBaseUrlInputChange"
                                    @keydown.enter="onPeerBaseUrlApply"
                                    placeholder="https://twicc.example.com"
                                    size="small"
                                ></wa-input>
                                <!-- eslint-enable @intlify/vue-i18n/no-raw-text -->
                                <wa-button
                                    size="small"
                                    variant="neutral"
                                    @click="onPeerBaseUrlApply"
                                >
                                    <wa-icon :name="peerBaseUrlApplyIcon" slot="start"></wa-icon>
                                    {{ $t('common.actions.apply') }}
                                </wa-button>
                            </div>
                            <wa-callout v-if="peerBaseUrlError" variant="danger" size="small">{{ peerBaseUrlError }}</wa-callout>
                            <wa-callout v-if="peerBaseUrlWarning" variant="warning" size="small">{{ peerBaseUrlWarning }}</wa-callout>
                            <wa-callout v-if="peerBaseUrlConfirmation" variant="warning" size="small">
                                <div class="peer-address-confirmation">
                                    <span>
                                        {{ $t('settings.peers.address.changeWarning') }}
                                    </span>
                                    <div class="peer-address-confirmation__actions">
                                        <wa-button size="small" variant="brand" @click="confirmPeerBaseUrlApply">{{ $t('common.actions.continue') }}</wa-button>
                                        <wa-button
                                            size="small"
                                            variant="neutral"
                                            appearance="outlined"
                                            @click="cancelPeerBaseUrlApply"
                                        >{{ $t('common.actions.cancel') }}</wa-button>
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
                                {{ $t('settings.peers.address.useExternalAddress') }}
                            </button>
                            <span class="setting-group-hint">
                                {{ $t('settings.peers.address.hint') }}
                            </span>
                        </div>
                    </section>

                    <!-- Sessions Section -->
                    <section v-if="activeSection === 'sessions'" class="settings-section">
                        <h3 class="settings-section-title">{{ $t('common.entities.sessions') }}</h3>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.sessions.displayMode.label') }}</label>
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
                            <label class="setting-group-label">{{ $t('settings.sessions.diffs.label') }}</label>
                            <wa-switch
                                :checked="showDiffs"
                                @change="onShowDiffsChange"
                                size="small"
                            >{{ $t('settings.sessions.diffs.autoOpenEdits') }}</wa-switch>
                            <wa-switch
                                :checked="toolDiffWordWrap"
                                @change="onToolDiffWordWrapChange"
                                size="small"
                            >{{ $t('common.display.wordWrap') }}</wa-switch>
                            <wa-switch
                                :checked="toolDiffSideBySide"
                                @change="onToolDiffSideBySideChange"
                                size="small"
                            >{{ $t('settings.sessions.diffs.sideBySide') }}</wa-switch>
                            <span class="setting-group-hint">{{ $t('common.display.sideBySideNarrowHint') }}</span>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.sessions.timestamps.label') }}</label>
                            <wa-switch
                                :checked="showMessageTimestamps"
                                @change="onShowMessageTimestampsChange"
                                size="small"
                            >{{ $t('settings.sessions.timestamps.showTime') }}</wa-switch>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.sessions.autoUnpinOnArchive.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                            <wa-switch
                                :checked="autoUnpinOnArchive"
                                @change="onAutoUnpinOnArchiveChange"
                                size="small"
                            >{{ $t('common.status.enabled') }}</wa-switch>
                        </div>
                        <wa-divider></wa-divider>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.sessions.compactList.label') }}</label>
                            <wa-switch
                                :checked="compactSessionList"
                                @change="onCompactSessionListChange"
                                size="small"
                            >{{ $t('common.status.enabled') }}</wa-switch>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('session.list.showAsTree') }}</label>
                            <wa-switch
                                :checked="showSessionsAsTree"
                                @change="onShowSessionsAsTreeChange"
                                size="small"
                            >{{ $t('common.status.enabled') }}</wa-switch>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('session.list.showProviderIcons') }}</label>
                            <wa-switch
                                :checked="showProviderIcons"
                                @change="onShowProviderIconsChange"
                                size="small"
                            >{{ $t('common.status.enabled') }}</wa-switch>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('session.list.activeAcrossProjects.label') }}</label>
                            <wa-switch
                                :checked="showActiveAcrossFilters"
                                @change="onShowActiveAcrossFiltersChange"
                                size="small"
                            >{{ $t('common.status.enabled') }}</wa-switch>
                            <span class="setting-group-hint">{{ $t('session.list.activeAcrossProjects.hint') }}</span>
                        </div>
                        <wa-divider></wa-divider>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.sessions.cache.label', { count: maxCachedSessions }) }}</label>
                            <wa-slider
                                :min.prop="1"
                                :max.prop="50"
                                :step.prop="1"
                                :value.prop="maxCachedSessions"
                                @input="onMaxCachedSessionsChange"
                                size="small"
                            ></wa-slider>
                            <span class="setting-group-hint">{{ $t('settings.sessions.cache.hint') }}</span>
                        </div>
                    </section>

                    <!-- Layouts Section -->
                    <section v-if="activeSection === 'layouts'" class="settings-section">
                        <h3 class="settings-section-title">{{ $t('settings.sections.layouts') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('common.layouts.defaultForNewSessions') }}</label>
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
                            <i18n-t keypath="settings.layouts.saveHint" scope="global" tag="span" class="setting-group-hint">
                                <template #button><wa-icon name="chevron-down" class="inline-hint-icon"></wa-icon></template>
                            </i18n-t>
                        </div>
                        <div class="setting-group">
                            <wa-button appearance="accent" size="small" @click="onManageLayouts">
                                <wa-icon slot="start" name="sliders"></wa-icon>
                                {{ $t('settings.layouts.manage') }}
                            </wa-button>
                        </div>
                    </section>

                    <!-- Title Suggestion Section -->
                    <section v-if="activeSection === 'title'" class="settings-section">
                        <h3 class="settings-section-title">{{ $t('settings.sections.titleSuggestion') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>
                        <div class="setting-group">
                            <wa-switch
                                :checked="titleGenerationEnabled"
                                @change="onTitleGenerationChange"
                                aria-describedby="title-generation-hint"
                                size="small"
                            >{{ $t('settings.titles.generation.label') }}</wa-switch>
                            <span id="title-generation-hint" class="setting-group-hint">
                                {{ $t('settings.titles.generation.hint') }}
                            </span>
                            <wa-switch
                                v-if="titleGenerationEnabled"
                                :checked="titleAutoApply"
                                @change="onTitleAutoApplyChange"
                                aria-describedby="title-auto-apply-hint"
                                size="small"
                            >{{ $t('settings.titles.automaticTitles') }}</wa-switch>
                            <span v-if="titleGenerationEnabled" id="title-auto-apply-hint" class="setting-group-hint">
                                {{ $t('settings.titles.automaticTitlesHint') }}
                            </span>
                            <label class="setting-group-label">{{ $t('common.labels.model') }}</label>
                            <SegmentedControl
                                fit
                                :label="$t('common.labels.model')"
                                :model-value="titleSuggestionModel"
                                :options="titleModelOptions"
                                @update:model-value="store.setTitleSuggestionModel($event)"
                                @click="onTitleSuggestionModelClick"
                                @keydown="onTitleSuggestionModelKeydown"
                            />
                            <!-- Why a model is not selectable (the tooltip of its segment is not reachable on a phone). -->
                            <span
                                v-for="(reason, model) in disabledTitleSuggestionModels"
                                :key="model"
                                class="setting-group-hint"
                            >{{ $t('settings.titles.model.unavailable', { model: TITLE_SUGGESTION_MODEL_LABELS[model], reason }) }}</span>
                            <HelpFeatureLink help-key="openrouter" :label="$t('help.openrouter')" />
                            <span v-if="titleGenerationEnabled" class="setting-group-hint">
                                {{ $t('settings.titles.model.fallbackHint') }}
                                <template v-if="titleSuggestionModelIsDisplaced">
                                    <template v-if="displacedTitleModelIsOpenRouter">
                                        {{ $t('settings.titles.model.openRouterSetupHint') }}
                                    </template>
                                    <template v-else>
                                        {{ $t('settings.titles.model.disabledProviderHint') }}
                                    </template>
                                </template>
                            </span>
                            <div v-if="titleGenerationEnabled" class="title-prompt-section">
                                <label class="setting-group-label">{{ $t('settings.titles.systemPrompt.label') }}</label>
                                <wa-textarea
                                    :value.prop="titleSystemPromptInput"
                                    @input="onTitleSystemPromptChange"
                                    size="small"
                                    rows="7"
                                    resize="vertical"
                                    class="title-prompt-textarea"
                                ></wa-textarea>
                                <div class="title-prompt-hint">
                                    <i18n-t keypath="settings.titles.systemPrompt.hint" tag="span" scope="global">
                                        <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- This is the literal system prompt placeholder. -->
                                        <template #placeholder><code>{text}</code></template>
                                        <template #apply>{{ $t('common.actions.apply') }}</template>
                                    </i18n-t>
                                    <div class="title-prompt-actions">
                                        <wa-button
                                            v-if="!isDefaultPrompt"
                                            variant="neutral"
                                            appearance="outlined"
                                            size="small"
                                            @click.stop="resetTitleSystemPrompt"
                                        >{{ $t('common.actions.resetToDefault') }}</wa-button>
                                        <wa-button
                                            size="small"
                                            variant="neutral"
                                            @click.stop="onTitleSystemPromptApply"
                                        >
                                            <wa-icon :name="titleSystemPromptApplyIcon" slot="start"></wa-icon>
                                            {{ $t('common.actions.apply') }}
                                        </wa-button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </section>

                    <!-- Editor Section -->
                    <section v-if="activeSection === 'editor'" class="settings-section">
                        <h3 class="settings-section-title">{{ $t('settings.sections.editor') }}</h3>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.editor.display.label') }}</label>
                            <wa-switch
                                :checked="editorWordWrap"
                                @change="onEditorWordWrapChange"
                                size="small"
                            >{{ $t('common.display.wordWrap') }}</wa-switch>
                            <wa-switch
                                :checked="diffSideBySide"
                                @change="onDiffSideBySideChange"
                                size="small"
                            >{{ $t('settings.editor.display.sideBySide') }}</wa-switch>
                            <span class="setting-group-hint">{{ $t('common.display.sideBySideNarrowHint') }}</span>
                        </div>
                    </section>

                    <!-- Terminal Section -->
                    <section v-if="activeSection === 'terminal'" class="settings-section">
                        <h3 class="settings-section-title">{{ $t('settings.sections.terminal') }}</h3>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.terminal.persistence.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                            <wa-switch
                                :checked="terminalUseTmux"
                                @change="onTmuxChange"
                                size="small"
                            >{{ $t('common.status.enabled') }}</wa-switch>
                            <span class="setting-group-hint">{{ $t('settings.terminal.persistence.hint') }}</span>
                        </div>
                        <div class="setting-group" v-if="terminalUseTmux">
                            <label class="setting-group-label">{{ $t('settings.terminal.config.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                            <div class="usage-file-input-row">
                                <wa-input
                                    :value="tmuxConfigPathInput"
                                    @input="onTmuxConfigPathInputChange"
                                    @keydown.enter="onTmuxConfigPathApply"
                                    :placeholder="$t('settings.terminal.config.placeholder')"
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
                                    {{ $t('common.actions.apply') }}
                                </wa-button>
                            </div>
                            <i18n-t keypath="settings.terminal.config.hint" scope="global" tag="span" class="setting-group-hint">
                                <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- This is the literal tmux option. -->
                                <template #mouseOff><code>mouse off</code></template>
                            </i18n-t>
                            <wa-callout
                                v-if="tmuxConfigValidation && !tmuxConfigValidation.valid"
                                variant="danger"
                                size="small"
                                class="usage-file-validation"
                            >{{ resolveUiMessage(tmuxConfigValidation.message) }}</wa-callout>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.terminal.copyOnSelect.label') }}</label>
                            <wa-switch
                                :checked="terminalCopyOnSelect"
                                @change="onCopyOnSelectChange"
                                size="small"
                            >{{ $t('common.status.enabled') }}</wa-switch>
                            <span class="setting-group-hint">
                                <i18n-t keypath="settings.terminal.copyOnSelect.hint" scope="global" tag="span">
                                    <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- Keyboard shortcuts preserve their key notation. -->
                                    <template #shortcut><kbd>{{ isMac ? '⌘V' : 'Ctrl+V' }}</kbd></template>
                                </i18n-t>
                                <span v-if="isLinux"> {{ $t('settings.terminal.copyOnSelect.primaryClipboardHint') }}</span>
                            </span>
                        </div>
                        <div class="setting-group" v-if="isMac">
                            <label class="setting-group-label">{{ $t('settings.terminal.optionKey.label') }}</label>
                            <wa-switch
                                :checked="terminalMacOptionIsMeta"
                                @change="onMacOptionIsMetaChange"
                                size="small"
                            >{{ $t('settings.terminal.optionKey.useAsMeta') }}</wa-switch>
                            <i18n-t keypath="settings.terminal.optionKey.hint" scope="global" tag="span" class="setting-group-hint">
                                <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- This is the literal shell keyboard shortcut. -->
                                <template #back><kbd>⌥B</kbd></template>
                                <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- This is the literal shell keyboard shortcut. -->
                                <template #forward><kbd>⌥F</kbd></template>
                                <template #lastArgument><kbd>⌥.</kbd></template>
                                <template #pipe><code>|</code></template>
                                <template #brace><code>{</code></template>
                                <template #backslash><code>\</code></template>
                            </i18n-t>
                        </div>
                    </section>

                    <!-- Tips Section -->
                    <TipsSettings v-if="activeSection === 'tips'" />

                    <HelpSettings v-if="activeSection === 'help'" />

                    <!-- Providers quotas/usage Section -->
                    <section v-if="activeSection === 'usage'" class="settings-section">
                        <h3 class="settings-section-title">{{ $t('settings.sections.providerUsage') }}</h3>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.usage.extraQuota.label') }}</label>
                            <wa-switch
                                :checked="extraUsageOnlyWhenNeeded"
                                @change="onExtraUsageOnlyWhenNeededChange"
                                size="small"
                            >{{ $t('settings.usage.extraQuota.onlyWhenNeeded') }}</wa-switch>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.usage.fiveHourQuota.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                            <wa-switch
                                :checked="hideFiveHourUsage"
                                @change="onHideFiveHourUsageChange"
                                size="small"
                            >{{ $t('settings.usage.fiveHourQuota.hide') }}</wa-switch>
                            <span class="setting-group-hint">
                                {{ $t('settings.usage.fiveHourQuota.hint', enabledProviders.size) }}
                            </span>
                        </div>
                        <div class="setting-group">
                            <label class="setting-group-label">{{ $t('settings.usage.extraUsageNotification.label') }}</label>
                            <wa-switch
                                :checked="notifyOnExtraUsageStart"
                                @change="onNotifyOnExtraUsageStartChange"
                                size="small"
                            >{{ $t('settings.usage.extraUsageNotification.notify') }}</wa-switch>
                            <i18n-t keypath="settings.usage.extraUsageNotification.hint" tag="span" class="setting-group-hint" scope="global">
                                <template #notificationsLink><a href="#" @click.prevent="selectSection('notifications')">{{ $t('settings.sections.notifications') }}</a></template>
                            </i18n-t>
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
                                <label class="setting-group-label">{{ $t('settings.usage.wakeUp.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                                <div class="wakeup-time-row">
                                    <wa-select
                                        :value="getWakeupHour(provider)"
                                        @change="onWakeupHourChange(provider, $event)"
                                        size="small"
                                    >
                                        <wa-option value="">{{ $t('common.status.off') }}</wa-option>
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
                                >{{ $t('settings.usage.readFile.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></wa-switch>
                                <template v-if="getReadEnabled(provider)">
                                    <div class="usage-file-input-row">
                                        <!-- eslint-disable @intlify/vue-i18n/no-raw-text -- This placeholder is an example filesystem path. -->
                                        <wa-input
                                            :value="usageFilePathInput[provider] ?? ''"
                                            @input="onUsageFilePathInputChange(provider, $event)"
                                            @keydown.enter="onUsageFilePathApply(provider)"
                                            placeholder="/path/to/usage.json"
                                            size="small"
                                            :disabled="!!usageFileValidating[provider]"
                                        ></wa-input>
                                        <!-- eslint-enable @intlify/vue-i18n/no-raw-text -->
                                        <wa-button
                                            size="small"
                                            variant="neutral"
                                            @click="onUsageFilePathApply(provider)"
                                            :disabled="!!usageFileValidating[provider]"
                                        >
                                            <wa-spinner v-if="usageFileValidating[provider]" slot="start"></wa-spinner>
                                            <wa-icon v-else :name="readApplyIcon(provider)" slot="start"></wa-icon>
                                            {{ $t('common.actions.apply') }}
                                        </wa-button>
                                    </div>
                                    <span class="setting-group-hint">{{ $t('settings.usage.filePath.hint') }}</span>
                                    <wa-callout
                                        v-if="usageFileValidation[provider] && !usageFileValidation[provider].valid"
                                        variant="danger"
                                        size="small"
                                        class="usage-file-validation"
                                    >{{ resolveUiMessage(usageFileValidation[provider].message) }}</wa-callout>
                                </template>
                            </div>
                            <div class="setting-group">
                                <wa-switch
                                    :checked="getDumpEnabled(provider)"
                                    @change="onUsageDumpEnabledChange(provider, $event)"
                                    size="small"
                                    :disabled="getReadEnabled(provider)"
                                >{{ $t('settings.usage.dumpFile.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></wa-switch>
                                <template v-if="getDumpEnabled(provider)">
                                    <div class="usage-file-input-row">
                                        <!-- eslint-disable @intlify/vue-i18n/no-raw-text -- This placeholder is an example filesystem path. -->
                                        <wa-input
                                            :value="usageDumpPathInput[provider] ?? ''"
                                            @input="onUsageDumpPathInputChange(provider, $event)"
                                            @keydown.enter="onUsageDumpPathApply(provider)"
                                            placeholder="/path/to/usage-dump.json"
                                            size="small"
                                            :disabled="!!usageDumpValidating[provider]"
                                        ></wa-input>
                                        <!-- eslint-enable @intlify/vue-i18n/no-raw-text -->
                                        <wa-button
                                            size="small"
                                            variant="neutral"
                                            @click="onUsageDumpPathApply(provider)"
                                            :disabled="!!usageDumpValidating[provider]"
                                        >
                                            <wa-spinner v-if="usageDumpValidating[provider]" slot="start"></wa-spinner>
                                            <wa-icon v-else :name="dumpApplyIcon(provider)" slot="start"></wa-icon>
                                            {{ $t('common.actions.apply') }}
                                        </wa-button>
                                    </div>
                                    <span class="setting-group-hint">{{ $t('settings.usage.filePath.hint') }}</span>
                                    <wa-callout
                                        v-if="usageDumpValidation[provider] && !usageDumpValidation[provider].valid"
                                        variant="danger"
                                        size="small"
                                        class="usage-file-validation"
                                    >{{ resolveUiMessage(usageDumpValidation[provider].message) }}</wa-callout>
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
                            <label class="setting-group-label">{{ $t('settings.usage.wakeUp.helpTitle') }}</label>
                            <span class="setting-group-hint">
                                {{ $t('settings.usage.wakeUp.helpDescription') }}
                            </span>
                        </div>
                        <div class="setting-group usage-mode-explanation">
                            <label class="setting-group-label">{{ $t('settings.usage.readFile.helpTitle') }}</label>
                            <span class="setting-group-hint">
                                {{ $t('settings.usage.readFile.helpDescription') }}
                            </span>
                        </div>
                        <div class="setting-group usage-mode-explanation">
                            <label class="setting-group-label">{{ $t('settings.usage.dumpFile.helpTitle') }}</label>
                            <span class="setting-group-hint">
                                {{ $t('settings.usage.dumpFile.helpDescription') }}
                            </span>
                        </div>
                    </section>

                    <!-- Keyboard Shortcuts Section -->
                    <section v-if="activeSection === 'shortcuts'" class="settings-section shortcuts-section">
                        <h3 class="settings-section-title">{{ $t('settings.shortcuts.title') }}</h3>
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
                        </Transition>
                        </div>
                    </div>
                </div>
            </div>
            <wa-divider></wa-divider>
            <p class="settings-notice">
                <wa-icon name="cloud" class="synced-icon"></wa-icon>
                {{ $t('settings.syncHint') }}
            </p>
            <wa-divider></wa-divider>
            <footer v-if="currentVersion" class="settings-footer">
                <span class="settings-footer-version">
                    <BrandLogo :size="16" class="settings-footer-logo" />
                    <a href="https://github.com/twidi/twicc/" target="_blank" rel="noopener">{{ $t('settings.about.currentVersion', { version: currentVersion }) }}</a><template v-if="store.isDevMode"> {{ $t('settings.about.devBadge') }}</template>
                    <template v-if="latestVersion">
                        &rarr;
                        <wa-button
                            class="settings-footer-update"
                            variant="brand"
                            appearance="accent"
                            size="small"
                            @click="onShowUpdateInstructions"
                        >
                            <wa-icon slot="start" name="arrow-up"></wa-icon>
                            {{ $t('settings.about.updateAvailable', { version: latestVersion.version }) }}
                        </wa-button>
                    </template>
                </span>
                ·
                <a href="#" class="settings-footer-changes" @click.prevent="openChangelog()">{{ $t('settings.about.changelog') }}</a>
                ·
                <a :href="SPONSOR_URL" target="_blank" rel="noopener" class="settings-footer-sponsor">
                    <span class="settings-footer-sponsor-icon"></span>
                    {{ $t('settings.about.sponsor') }}
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
                <AppTooltip v-if="currentStatusDisplay && hasMultipleStatusProviders" :for="statusNextButtonId">{{ $t('settings.status.nextProvider') }}</AppTooltip>
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
                <AppTooltip v-if="showLogout" :for="logoutButtonId">{{ $t('auth.logout') }}</AppTooltip>
            </footer>
        </wa-popover>
        <ChangelogDialog ref="changelogDialogRef" @close="onChangelogClose" />
        <LayoutManagerDialog ref="layoutManagerDialogRef" />
        <ShareManagerDialog :open="showShareManager" @close="showShareManager = false" />
        <TelemetryPayloadDialog :open="showTelemetryPayload" @close="showTelemetryPayload = false" />
    </Teleport>
</template>

<style scoped>
#settings-trigger::part(label) {
    display: flex;
    gap: var(--wa-space-s);
}

#settings-trigger.settings-trigger--icon-only::part(base) {
    width: var(--rail-button-size);
    height: var(--rail-button-size);
    padding: 0;
    border: 0;
    line-height: 1;
    font-size: var(--rail-icon-size);
    color: var(--rail-button-color);
    background-color: var(--rail-button-bg, transparent);
    background-image: none;
    scale: none;
    transition: none;
}

@media (hover: hover) {
    #settings-trigger.settings-trigger--icon-only:hover::part(base) {
        background-image: linear-gradient(var(--rail-button-fill-hover), var(--rail-button-fill-hover));
    }
}

#settings-trigger.settings-trigger--icon-only:active::part(base) {
    background-image: linear-gradient(var(--rail-button-fill-hover), var(--rail-button-fill-hover));
}

.settings-trigger--icon-only > .settings-trigger-label {
    position: absolute;
    inline-size: 1px;
    block-size: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
}

/* A quarter turn of the gear on hover. 600ms, longer than the motion tokens: a slow turn
   reads as a flourish. */
#settings-trigger > wa-icon[name='gear'] {
    transition: rotate 600ms var(--motion-ease-out);
}
@media (hover: hover) {
    #settings-trigger:hover > wa-icon[name='gear'] {
        rotate: calc(90deg * var(--motion-amount));
    }
}

.settings-popover {
    --max-width: v-bind(popoverWidth);
    --arrow-size: 16px;
}

.settings-popover::part(body) {
    padding: 0;
    box-sizing: border-box;
    max-height: var(--auto-size-available-height, calc(100dvh - 32px));
    overflow: auto;
}

.settings-popover > :not(.settings-layout) {
    flex-shrink: 0;
}

/* -- Master-detail layout -- */

.settings-layout {
    display: flex;
    flex-direction: column;
    overflow: hidden;
    /* Keep a usable panel when the footer needs more space; the body then scrolls. */
    min-height: min(8rem, var(--auto-size-available-height, calc(100dvh - 32px)));
    height: min(calc(90dvh - 8rem), 50rem);
    width: min(v-bind(popoverWidth), v-bind(popoverMaxWidth));
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
    /* The gliding fill under the chosen section (motion.css .glide-ink), lit (step 6a). */
    position: relative;
    --glide-ink-bg: linear-gradient(100deg, var(--wa-color-brand-fill-normal), var(--wa-color-brand-fill-quiet));
    --glide-ink-radius: var(--wa-border-radius-m);
}

.settings-nav > .glide-ink {
    box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--wa-color-brand-60) 35%, transparent);
}

.settings-nav-item {
    all: unset;
    /* After `all: unset`, which would reset it: the item paints above the ink. */
    position: relative;
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

/* Not on the chosen section: the item paints above the ink, and a flat tint would hide
   its gradient. */
.settings-nav-item:not(.active):hover {
    background: var(--glass-item-hover);
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

/* Section crossfade (step 7f): a fast fade out, then a fade in with a small rise. No mobile
   media query: `:css="!isNarrow"` turns the classes off on a phone. */
.settings-swap-leave-active {
    transition: opacity var(--motion-dur-1) ease-in-out;
}
.settings-swap-leave-to {
    opacity: 0;
}
.settings-swap-enter-active {
    transition:
        opacity var(--motion-dur-2) ease-in-out,
        translate var(--motion-dur-3) var(--motion-ease-out);
}
.settings-swap-enter-from {
    opacity: 0;
    translate: 0 calc(0.75rem * var(--motion-amount));
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
           stick it to the top of the scrolling panel. Its glass-sticky layer
           blurs the content scrolling underneath; using padding-bottom (rather
           than margin) extends that blurred area right down to the content, with
           no clear strip. z-index sits above the content and the scroll-shadow
           pseudo-elements (z-index: 2, styles/scroll-shadow.css): once stuck, the row
           covers the top scroll shadow, and marks the scroll itself. The row spreads over the panel's padding (negative margins,
           same padding back, stuck at minus that padding) so, once stuck, it covers
           the panel edge to edge: no strip of scrolling content above or beside it. */
        position: sticky;
        top: calc(-1 * var(--wa-space-s));
        z-index: 3;
        margin: calc(-1 * var(--wa-space-s)) calc(-1 * var(--wa-space-s)) 0;
        padding: var(--wa-space-s);
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

    /* A drill-down list: no chosen section to mark. */
    .settings-nav-ink {
        display: none;
    }

    /* No chosen section here: every item gets its hover tint back. */
    .settings-nav-item:hover {
        background: var(--glass-item-hover);
    }

    .settings-nav-item::after {
        content: '›';
        margin-left: auto;
        font-size: 1.3em;
        color: var(--wa-color-text-quiet);
    }
}

/* -- Settings notice (footer bar) -- */

.settings-notice {
    font-size: var(--wa-font-size-xs);
    line-height: 1.4;
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
    display: inline-flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs);
}

.settings-footer-update::part(base) {
    font-size: var(--wa-font-size-xs);
}

.settings-footer-logo {
    margin-right: var(--wa-space-2xs);
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
    line-height: 1.4;
    color: var(--wa-color-neutral-fill-loud);
}

.hint.danger {
    color: var(--wa-color-danger-fill-loud);
}

@media (width < 640px) {
    .settings-notice,
    .settings-footer {
        padding-block: 4px;
    }
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

/* The switch row closing the matrix + task-controls stack (provider sections): a
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
    /* Halve the group gap between a control and its following help text. */
    > :not(label):not(.setting-group-hint):has(+ .setting-group-hint) {
        margin-bottom: calc(-1 * var(--wa-space-2xs));
    }
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

/* A section title with its help link under it (Sharing, Peers, Text to Speech): on a
   phone the title hides, the link stays. */
.settings-sections .settings-section-heading {
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
    line-height: 1.4;
    color: var(--wa-color-text-quiet);
    font-style: italic;
}
/* Reset the native form-control height so the help icon fits the text line. */
.settings-sections .setting-group-hint .help-icon-button {
    height: auto;
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

</style>
