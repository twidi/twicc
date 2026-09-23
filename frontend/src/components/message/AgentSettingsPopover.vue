<script setup>
// Provider-agnostic agent-settings popover anchored on the trigger button
// rendered by MessageInput. Every render decision (which fields appear,
// which options are disabled, what the help text says, what the startup
// warning reads, which dialog the "Manage presets" button opens) is
// resolved through hooks on the session's provider helpers — see the
// "Agent settings popover/summary rendering hooks" section in
// ``BaseProviderHelpers``.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'
import { presetSummaryParts, bundleSummaryParts } from '../../utils/presetFormat'
import { DEFAULT_SENTINEL } from '../../composables/useSessionAgentSettings'
import { getProviderHelpers } from '../../providers'
import { useDataStore } from '../../stores/data'
import { useBenchmarkTaskStore } from '../../stores/benchmarkTask'
import AgentSettingsPresetsDialog from '../app/AgentSettingsPresetsDialog.vue'
import AgentSettingsSummaryView from './AgentSettingsSummaryView.vue'
import AgentSettingsMatrix from './AgentSettingsMatrix.vue'
import AgentSettingsBenchmarkTask from './AgentSettingsBenchmarkTask.vue'
import AgentSettingsSwitches from './AgentSettingsSwitches.vue'
import HelpTextLink from '../help/HelpTextLink.vue'
import ProviderIcon from '../ui/ProviderIcon.vue'
import PermissionModeIcon from '../ui/PermissionModeIcon.vue'
import { buildSwitchRows } from '../../utils/agentSwitchRows'

const props = defineProps({
    for: { type: String, required: true },
    session: { type: Object, default: null },
    settings: { type: Object, required: true },
    isDraft: { type: Boolean, default: false },
    messageText: { type: String, default: '' },
    buttonLabel: { type: String, default: 'Send' },
    // True while a pending request locks sending: the Send/Apply button is hidden,
    // so the "click X to apply" hint is replaced with one explaining the changes
    // will apply once the request is answered.
    sendingLocked: { type: Boolean, default: false },
})

const {
    isStarting,
    processState,
    selectedModel,
    selectedPermissionMode,
    selectedEffort,
    selectedThinking,
    selectedClaudeInChrome,
    selectedContextMax,
    SELECTED_REFS,
    summaryState,
    resolvedDefaults,
    effectiveModel,
    isContextMaxForced,
    sessionIsUntrusted,
    isPermissionModeForced,
    clampedPermissionMode,
    anySettingForced,
    hasDropdownsChanged,
    presetGroups,
    presetsDialogOpen,
    handlePresetSelect,
    providerSwitcherOptions,
    matrixBlocks,
    matrixEffortColumns,
    matrixDefaultCell,
    restoreSettings,
    resetAllToDefaults,
    startupChanges,
    providerHelpers,
} = props.settings

const dataStore = useDataStore()
const taskStore = useBenchmarkTaskStore()

// Non-image attachments currently held by the draft. Computed off the
// store's reactive Map so the labelled wa-callout below reacts to add /
// remove without ad-hoc wiring.
const nonImageAttachments = computed(() => {
    const sid = props.settings.sessionId.value
    if (!sid) return []
    return dataStore.getAttachments(sid).filter(m => m.type !== 'image')
})

// Provider id of the most recent rejected switch attempt — used to
// surface a transient warning callout asking the user to drop the
// blocking attachments before retrying. Cleared automatically when the
// blocking attachments are gone (so removing them via any other path,
// e.g. the attachments popover, also dismisses the callout).
const blockedSwitchTargetProvider = ref(null)
const blockedSwitchTargetLabel = computed(() => {
    const target = blockedSwitchTargetProvider.value
    if (!target) return null
    if (nonImageAttachments.value.length === 0) return null
    const opt = providerSwitcherOptions.value.find(o => o.value === target)
    return opt?.label ?? null
})

// Switch the draft to another provider. Intercepts the switch when the target
// can't accept the current non-image attachments: surfaces the callout and
// leaves the session on its current provider (the user drops the attachments via
// the callout's inline action, which retries, or backs out). Resets every per-
// session override so the bundle follows the new provider's defaults. Returns
// true when the switch actually happened, false when it was blocked or a no-op.
function switchToProvider(provider) {
    if (!provider || provider === props.session?.provider) return false
    const optHelpers = getProviderHelpers(provider)
    const support = optHelpers?.getAttachmentSupport() ?? null
    const docsBlocked = nonImageAttachments.value.length > 0 && support?.documents === false
    if (docsBlocked) {
        blockedSwitchTargetProvider.value = provider
        return false
    }
    blockedSwitchTargetProvider.value = null
    dataStore.setDraftProvider(props.settings.sessionId.value, provider)
    resetAllToDefaults()
    return true
}

// Matrix cell click: pick provider + model + effort at once. When the cell is
// in another provider's block (drafts only), switch the draft's provider first
// — that resets the non-matrix fields to the new provider's defaults — then
// override the model + effort with the chosen cell. The nextTick lets the
// session→refs watcher settle after the switch before we write the cell values.
async function onMatrixSelect({ provider, model, effort }) {
    if (provider !== props.session?.provider) {
        // Real sessions can't change provider (and only ever show their own
        // block); the switch is a draft-only affordance.
        if (!props.isDraft) return
        if (!switchToProvider(provider)) return
        await nextTick()
    }
    selectedModel.value = model
    selectedEffort.value = effort
}

// ─── Auto-select the best-scoring cell ───────────────────────────────────
// With "Auto-select best" on, pick the highest-scoring cell whenever the
// task controls change (or the switches toggle) — exactly as if the user clicked
// that square. "Default provider only" restricts the search to the user's
// default provider when several providers are shown.
function findBestCell() {
    const blocks = matrixBlocks.value
    if (!blocks?.length) return null
    let candidates = blocks
    if (taskStore.defaultProviderOnly && blocks.length > 1) {
        const defProvider = matrixDefaultCell.value?.provider
        candidates = blocks.filter(b => b.provider === defProvider)
    }
    let best = null
    for (const block of candidates) {
        for (const row of block.rows) {
            for (const cell of row.cells) {
                if (cell.enabled && cell.score != null && (best === null || cell.score > best.score)) {
                    best = { provider: block.provider, model: row.model, effort: cell.effort, score: cell.score }
                }
            }
        }
    }
    return best
}

watch(
    [
        () => taskStore.autoSelectBest,
        () => taskStore.defaultProviderOnly,
        () => taskStore.taskType,
        () => taskStore.difficulty,
        () => taskStore.favor,
    ],
    () => {
        if (!taskStore.autoSelectBest) return
        const best = findBestCell()
        if (best) onMatrixSelect({ provider: best.provider, model: best.model, effort: best.effort })
    },
    { flush: 'post' },
)

async function removeBlockingDocuments() {
    const sid = props.settings.sessionId.value
    if (!sid) return
    const target = blockedSwitchTargetProvider.value
    await dataStore.removeNonImageAttachments(sid)
    // Auto-retry the switch the user originally asked for so the click
    // sequence "select Codex → Remove them" doesn't require a third click.
    blockedSwitchTargetProvider.value = null
    if (target) switchToProvider(target)
}

// Fields below the matrix rendered as Default/Force-to wa-selects. Model +
// effort are owned by the matrix; the context/thinking/Chrome MCP/fast switches
// are built by ``buildSwitchRows`` (utils/agentSwitchRows.js). Only permission
// stays a select here.
const SELECT_FIELDS = ['permission_mode']

const defaults = computed(() => summaryState.value.defaults)

// The session's current EFFECTIVE value per wire field (the user's selection,
// else the resolved default). Fed to the preset / reset summaries so each can
// dashed-underline the fields that DIFFER from the current choice — i.e. what
// applying that preset / reset would actually change.
const currentEffective = computed(() => {
    const sel = summaryState.value.selected
    const def = summaryState.value.defaults
    const pick = (f) => sel[f] ?? def[f]
    return {
        selected_model: pick('selected_model'),
        context_max: pick('context_max'),
        effort: pick('effort'),
        thinking_enabled: pick('thinking_enabled'),
        permission_mode: pick('permission_mode'),
        claude_in_chrome: pick('claude_in_chrome'),
        fast_mode: pick('fast_mode'),
    }
})

// Render-time context fed to the rendering hooks. Hooks ignore keys they
// don't need; this lets us assemble it once per render and pass it through.
const baseContext = computed(() => ({
    sessionId: props.settings.sessionId.value,
    isStarting: isStarting.value,
    isContextMaxForced: isContextMaxForced.value,
    effectiveModel: effectiveModel.value,
    // Hybrid CLI sessions get their own live/idle/startup classification
    // (e.g. permission_mode becomes startup) — surfaced via field help text.
    isHybrid: props.session?.hybrid === true,
    // Trust clamp (trust design §13.3/§13.4): ``untrusted`` disables
    // permission modes outside the untrusted-allowed set (base
    // ``isChoiceDisabled``); the forced pair makes the select show the value
    // the backend actually applies (base ``getDisplayedSelectValue`` +
    // ``getFieldHelpText``), mirroring the isContextMaxForced machinery.
    untrusted: sessionIsUntrusted.value,
    permissionModeForced: isPermissionModeForced.value,
    clampedPermissionMode: clampedPermissionMode.value,
}))

function fieldContext(field) {
    return {
        ...baseContext.value,
        field,
        selectedValue: SELECTED_REFS[field]?.value ?? null,
        defaultValue: defaults.value[field],
    }
}

const startupSettingsWarning = computed(() => {
    if (!startupChanges.value?.startup.length) return null
    const helpers = providerHelpers.value
    if (!helpers) return null
    return helpers.getStartupWarningText({
        processStateName: processState.value?.state ?? null,
        hasMessageText: Boolean(props.messageText.trim()),
        hasCrons: (processState.value?.active_crons?.length ?? 0) > 0,
    })
})

// Only surfaces during ASSISTANT_TURN for idle-only diffs: in USER_TURN
// the next Send applies idle changes immediately via the SDK, and any
// concurrent startup change subsumes idle ones under the startup
// kill/restart — so the startup callout wins when both exist.
const idleSettingsWarning = computed(() => {
    if (!startupChanges.value?.idle.length) return null
    if (startupChanges.value?.startup.length) return null
    if (processState.value?.state !== 'assistant_turn') return null
    const helpers = providerHelpers.value
    if (!helpers) return null
    return helpers.getIdleWarningText({
        hasMessageText: Boolean(props.messageText.trim()),
    })
})

// Switch/toggle rows (context, thinking, Chrome MCP, fast mode) below the
// matrix — assembled by the shared ``buildSwitchRows`` and rendered by
// ``AgentSettingsSwitches``. ``valueFor`` feeds the session's current effective
// value per field.
const switchRows = computed(() =>
    buildSwitchRows(providerHelpers.value, {
        fieldContext,
        valueFor: (field) => currentEffective.value[field],
    }),
)

// Permission select rows, each on its own line below the switches. Kept inline
// (not shared): they carry the session-only Default/Force-to sentinel machinery.
const selectRows = computed(() => {
    const helpers = providerHelpers.value
    if (!helpers) return []
    const rows = []
    for (const field of SELECT_FIELDS) {
        if (!helpers.supportsAgentSetting(field)) continue
        const ctx = fieldContext(field)
        const label = helpers.getFieldLabel(field)
        const selectedValue = SELECTED_REFS[field]?.value ?? null
        rows.push({
            field, label,
            value: helpers.getDisplayedSelectValue(field, selectedValue, ctx),
            defaultLabel: helpers.getDefaultValueLabel(field, defaults.value[field]),
            fieldDisabled: helpers.isFieldDisabled(field, ctx),
            helpText: helpers.getFieldHelpText(field, ctx),
            // Glyph for the closed select: the effective mode (forced selection
            // or resolved default), so it always reflects what will apply.
            iconInfo: helpers.getChoiceIcon(field, currentEffective.value[field]),
            choices: helpers.getFieldChoices(field).map(opt => {
                const disabled = helpers.isChoiceDisabled(field, opt.value, ctx)
                const disabledReason = disabled ? helpers.getChoiceDisabledReason(field, opt.value, ctx) : null
                return {
                    value: String(opt.value),
                    label: opt.label,
                    description: disabledReason ?? opt.description ?? null,
                    labelWithSuffix: disabled ? `${opt.label} (not available)` : opt.label,
                    disabled,
                    icon: opt.icon ?? null,
                    color: opt.color ?? null,
                }
            }),
        })
    }
    return rows
})

function onSelectChange(field, event) {
    const ref_ = SELECTED_REFS[field]
    if (!ref_) return
    const raw = event.target.value
    if (raw === DEFAULT_SENTINEL) {
        // Snapshot model: "Default: X" pins the field to the current resolved
        // default (concrete), not a NULL "follow".
        ref_.value = defaults.value[field]
        return
    }
    // Look the option up by stringified value so the typed (boolean / number /
    // string) original is restored from the wa-select's string binding.
    const choices = providerHelpers.value?.getFieldChoices(field) ?? []
    const match = choices.find(opt => String(opt.value) === raw)
    ref_.value = match ? match.value : raw
}

// Apply a switch/toggle change emitted by AgentSettingsSwitches. The component
// already resolves the final typed value (boolean for a switch, big/small value
// for the context toggle), so we just write it to the field's selected ref.
function onSwitchRowChange({ field, value }) {
    const ref_ = SELECTED_REFS[field]
    if (ref_) ref_.value = value
}

function resetField(field) {
    const ref_ = SELECTED_REFS[field]
    if (ref_) ref_.value = defaults.value[field]
}

const popoverRef = ref(null)
let frozenAnchorRect = null
let resizeTimer = null

function handleShow(e) {
    // The trigger's width changes with the settings summary, and the Reset
    // button can appear beside it after the first edit. Freeze the trigger's
    // current viewport rect for this opening so Floating UI does not move the
    // popover while the controls underneath it reflow.
    if (e.target !== popoverRef.value) return
    const trigger = document.getElementById(props.for)
    frozenAnchorRect = trigger?.getBoundingClientRect() ?? null
}

function handleAfterHide(e) {
    // Restore the live element so the next opening starts from wherever the
    // trigger ended up after the previous settings changes.
    if (e.target !== popoverRef.value) return
    const pop = popoverRef.value
    if (resizeTimer !== null) clearTimeout(resizeTimer)
    resizeTimer = null
    frozenAnchorRect = null
    if (pop?.popup) pop.popup.anchor = pop.anchor
}

function repositionFromTrigger() {
    const pop = popoverRef.value
    if (!pop?.open || !pop.popup || !frozenAnchorRect) return
    const trigger = document.getElementById(props.for)
    if (!trigger) return
    // A real viewport resize is allowed to move the frozen reference: the
    // toolbar itself may have reflowed, and the old coordinates may now be
    // outside the viewport. Ordinary settings-driven reflows do not reach
    // this path, so they still leave the popover fixed.
    frozenAnchorRect = trigger.getBoundingClientRect()
    pop.popup.reposition()
}

function handleViewportResize() {
    if (resizeTimer !== null) clearTimeout(resizeTimer)
    // Browser window resizing can expose an intermediate layout for several
    // frames. Reposition after the resize settles, then take a second sample
    // because the first popup placement can itself finish a responsive reflow.
    resizeTimer = setTimeout(() => {
        repositionFromTrigger()
        resizeTimer = setTimeout(() => {
            resizeTimer = null
            repositionFromTrigger()
        }, 100)
    }, 100)
}

function focusFirstElement() {
    const pop = popoverRef.value
    if (!pop) return
    // wa-switch covers the two-provider toggle; wa-button[slot="trigger"]
    // targets the inner wa-dropdown triggers (>2 provider switcher,
    // Reset/Presets); wa-select covers the model row and simple-field rows.
    // First DOM match wins, which mirrors the top-to-bottom order of the popover.
    const first = pop.querySelector('wa-switch:not([disabled]), wa-button[slot="trigger"]:not([disabled]), wa-select:not([disabled])')
    first?.focus()
}

function handleAfterShow(e) {
    // Ignore wa-after-show events that bubble up from inner wa-select /
    // wa-dropdown menus — only the popover's own open event should trigger
    // first-element focus.
    if (e.target !== popoverRef.value) return
    const pop = popoverRef.value
    // WaPopover writes its live element anchor during the opening update, after
    // wa-show. Replace it only once that update and animation have completed.
    if (pop?.popup && frozenAnchorRect) {
        const initialRect = frozenAnchorRect
        pop.popup.anchor = { getBoundingClientRect: () => frozenAnchorRect ?? initialRect }
    }
    focusFirstElement()
}

function closeAndFocusTextarea() {
    // Pre-focus the textarea so vPopoverFocusFix lands focus there after
    // close — without this the focus would fall on a now-hidden element
    // inside the popover.
    document.querySelector('.message-input wa-textarea')?.focus()
    popoverRef.value?.hide()
}

function handleToggleShortcut() {
    const pop = popoverRef.value
    if (!pop) return
    if (pop.open) {
        closeAndFocusTextarea()
    } else {
        pop.show()
    }
}

function handlePopoverKeydown(e) {
    // Escape inside the popover: close it and return focus to the textarea.
    // Replaces the native dialog Escape close (which would put focus on the
    // trigger button instead) and prevents the Escape from reaching App.vue's
    // triple-Escape counter.
    if (e.key === 'Escape' && popoverRef.value?.open) {
        e.preventDefault()
        e.stopPropagation()
        closeAndFocusTextarea()
    }
}

onMounted(() => {
    window.addEventListener('twicc:toggle-agent-settings', handleToggleShortcut)
    window.addEventListener('resize', handleViewportResize)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:toggle-agent-settings', handleToggleShortcut)
    window.removeEventListener('resize', handleViewportResize)
    if (resizeTimer !== null) clearTimeout(resizeTimer)
})
</script>

<template>
    <wa-popover
        ref="popoverRef"
        v-popover-focus-fix
        @wa-show="handleShow"
        @wa-after-show="handleAfterShow"
        @wa-after-hide="handleAfterHide"
        @keydown="handlePopoverKeydown"
        :for="props.for"
        placement="top"
        class="settings-popover"
    >
        <!-- Provider switch blocked by non-image attachments — appears
             only after the user actually attempts a switch to a
             provider that doesn't accept the current attachments, so an
             otherwise-uninterested user never sees the callout. -->
        <wa-callout
            v-if="isDraft && blockedSwitchTargetLabel"
            variant="warning"
            class="provider-blocked-callout"
        >
            <wa-icon name="triangle-exclamation" slot="icon"></wa-icon>
            {{ blockedSwitchTargetLabel }}
            cannot accept the {{ nonImageAttachments.length }} non-image
            attachment{{ nonImageAttachments.length > 1 ? 's' : '' }} on this draft.
            <a class="settings-action-link" @click.prevent="removeBlockingDocuments">
                Remove {{ nonImageAttachments.length > 1 ? 'them' : 'it' }} and switch
            </a>
        </wa-callout>

        <!-- Reset / Presets (non-scrollable). The provider selector lives in the
             matrix below now — provider, model and effort are picked there. -->
        <div class="settings-panel-presets">
            <!-- Reset / Presets: one dropdown grouped by provider. Each group
                 leads with a "{provider} default" reset entry, then that
                 provider's presets. A draft shows every switchable provider
                 (picking one switches the draft first); a real session lists
                 only its own. See useSessionAgentSettings.presetGroups. -->
            <wa-dropdown @wa-select="handlePresetSelect" class="presets-dropdown">
                <wa-button slot="trigger" size="small" appearance="outlined" :disabled="isStarting" class="presets-trigger">
                    <span class="presets-trigger-label"><wa-icon name="sliders"></wa-icon> Reset / Presets</span>
                    <wa-icon slot="end" name="caret-down"></wa-icon>
                </wa-button>
                <template v-for="(group, gi) in presetGroups" :key="group.provider">
                    <wa-divider v-if="gi > 0"></wa-divider>
                    <!-- Group header: only when more than one provider is shown -->
                    <wa-dropdown-item v-if="presetGroups.length > 1" disabled class="group-header">
                        <ProviderIcon
                            v-if="group.icon"
                            slot="icon"
                            :provider="group.provider"
                        />
                        {{ group.label }}
                    </wa-dropdown-item>
                    <!-- Reset targets: "{provider} default" always, plus the
                         ancestor / global levels when a project sets its own
                         defaults. See useSessionAgentSettings.resetTargetsForProvider. -->
                    <wa-dropdown-item
                        v-for="(target, ti) in group.resetTargets"
                        :key="target.key"
                        :value="`reset:${group.provider}:${ti}`"
                        :disabled="group.isCurrent && group.resetTargets.length === 1 && !anySettingForced"
                        class="reset-item"
                    >
                        <wa-icon slot="icon" name="arrow-rotate-left"></wa-icon>
                        <span>{{ target.label }}</span>
                        <AgentSettingsSummaryView
                            class="option-description"
                            :parts="bundleSummaryParts(target.bundle, getProviderHelpers(group.provider), {
                                current: group.isCurrent ? currentEffective : null,
                            })"
                        />
                    </wa-dropdown-item>
                    <wa-dropdown-item
                        v-for="p in group.presets"
                        :key="p.index"
                        :value="`preset:${group.provider}:${p.index}`"
                        class="preset-item"
                    >
                        <span>{{ p.preset.name }}</span>
                        <AgentSettingsSummaryView
                            class="option-description"
                            :parts="presetSummaryParts(p.preset, getProviderHelpers(group.provider), {
                                defaults: group.defaults,
                                current: group.isCurrent ? currentEffective : null,
                                untrusted: sessionIsUntrusted,
                            })"
                        />
                    </wa-dropdown-item>
                </template>
                <wa-divider></wa-divider>
                <wa-dropdown-item value="__manage__">
                    <wa-icon slot="icon" name="pen-to-square"></wa-icon>
                    Manage presets
                </wa-dropdown-item>
            </wa-dropdown>
        </div>

        <!-- Actions & callouts (non-scrollable) — hidden on drafts since there's no process to apply to -->
        <div v-if="(!isDraft && hasDropdownsChanged) || startupSettingsWarning || idleSettingsWarning" class="settings-panel-actions">
            <div v-if="!isDraft && hasDropdownsChanged" class="settings-panel-links">
                <a class="settings-action-link" @click.prevent="restoreSettings">
                    <wa-icon name="xmark"></wa-icon>
                    Discard unsaved changes
                </a>
            </div>
            <wa-callout v-if="!isDraft && hasDropdownsChanged" variant="brand" class="settings-info-callout">
                <wa-icon name="circle-info" slot="icon"></wa-icon>
                <template v-if="sendingLocked">Your changes are saved and will apply once you answer the pending request.</template>
                <template v-else>Click "{{ buttonLabel }}" to apply your changes.</template>
            </wa-callout>
            <wa-callout v-if="startupSettingsWarning" variant="warning" class="startup-warning-callout">
                <wa-icon name="triangle-exclamation" slot="icon"></wa-icon>
                {{ startupSettingsWarning }}
            </wa-callout>
            <wa-callout v-else-if="idleSettingsWarning" variant="warning" class="idle-warning-callout">
                <wa-icon name="triangle-exclamation" slot="icon"></wa-icon>
                {{ idleSettingsWarning }}
            </wa-callout>
            <!-- Hybrid CLI advisory: TwiCC never reads back TUI-side changes,
                 so settings must be driven from here. -->
            <wa-callout v-if="session?.hybrid" variant="neutral" class="hybrid-settings-note">
                <wa-icon name="terminal" slot="icon"></wa-icon>
                Hybrid CLI session: change settings here rather than inside the
                terminal — TwiCC does not read back TUI-side changes. Changes
                apply on the next message; some restart the CLI.
            </wa-callout>
        </div>

        <!-- Settings dropdowns (scrollable) -->
        <div class="settings-panel">
            <!-- Heading above the matrix + a link to the score help page. -->
            <div class="matrix-heading">
                <span class="setting-label">Model &amp; effort picker</span>
                <HelpTextLink help-key="model-effort-score" label="What are those numbers?" />
            </div>

            <!-- Provider × model × effort matrix — owns those three fields. -->
            <AgentSettingsMatrix
                :blocks="matrixBlocks"
                :effort-columns="matrixEffortColumns"
                @select="onMatrixSelect"
            />

            <!-- Task controls driving the matrix's benchmark scores. -->
            <AgentSettingsBenchmarkTask :provider-count="matrixBlocks.length" />

            <!-- Switches (context toggle, thinking, Chrome MCP, fast mode) share
                 one wrapping flex row; the permission select renders below. -->
            <AgentSettingsSwitches :rows="switchRows" @change="onSwitchRowChange" />

            <!-- Select rows (permission) — each on its own row, at the end -->
            <div
                v-for="row in selectRows"
                :key="row.field"
                class="setting-row"
            >
                <label class="setting-label">{{ row.label }}</label>
                <wa-select
                    :value.prop="row.value"
                    @change="onSelectChange(row.field, $event)"
                    size="small"
                    :disabled="row.fieldDisabled"
                >
                    <PermissionModeIcon
                        v-if="row.iconInfo"
                        slot="start"
                        :icon="row.iconInfo.icon"
                        :color="row.iconInfo.color"
                    />
                    <wa-option :value="DEFAULT_SENTINEL">Default: {{ row.defaultLabel }}</wa-option>
                    <small class="select-group-label">Force to:</small>
                    <wa-option
                        v-for="opt in row.choices"
                        :key="opt.value"
                        :value="opt.value"
                        :label="opt.labelWithSuffix"
                        :disabled="opt.disabled"
                    >
                        <PermissionModeIcon
                            v-if="opt.icon"
                            :icon="opt.icon"
                            :color="opt.color"
                            class="permission-option-icon"
                        />
                        <span>{{ opt.labelWithSuffix }}</span>
                        <span v-if="opt.description" class="option-description">{{ opt.description }}</span>
                    </wa-option>
                </wa-select>
                <span v-if="row.helpText" class="setting-help">{{ row.helpText }}</span>
                <a v-else-if="row.value !== DEFAULT_SENTINEL" class="reset-setting-link" @click.prevent="resetField(row.field)">Reset to default: {{ row.defaultLabel }}</a>
            </div>
        </div>

        <AgentSettingsPresetsDialog
            v-if="session?.provider"
            v-model:open="presetsDialogOpen"
            :provider="session.provider"
            :resolved-defaults="resolvedDefaults"
            :untrusted="sessionIsUntrusted"
        />
    </wa-popover>
</template>

<style scoped>
.settings-popover {
    --max-width: min(30rem, calc(100vw - 2rem));
    --arrow-size: 12px;
    &::part(body) {
        /* Keep the horizontal geometry stable when pending-change actions
           appear. The Web Awesome default is width:max-content, which made
           this panel grow from its matrix width to --max-width and recenter. */
        width: var(--max-width);
        max-height: calc(100vh - 8rem);
        display: flex;
        flex-direction: column;
    }
}

@media (width <= 400px) {
    .settings-popover {
        /* At phone width, touch both viewport edges instead of retaining the
           desktop gutter. Floating UI's shift middleware pins the panel at 0. */
        --max-width: 100vw;

        &::part(body) {
            padding: var(--wa-space-xs);
        }
    }

    .settings-panel :deep(.matrix-row-header) {
        gap: var(--wa-space-3xs);
    }

    .settings-panel :deep(.matrix-grid) {
        gap: 1px;
    }
}

.settings-panel {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    overflow-y: auto;
    flex: 1;
    min-height: 0;
}

/* Heading above the matrix ("Model & effort" + help link). Pull the matrix up
   to a tight gap under it — the panel's uniform space-l is too airy there. */
.matrix-heading {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    column-gap: var(--wa-space-s);
    row-gap: var(--wa-space-3xs);
    margin-bottom: calc(var(--wa-space-2xs) - var(--wa-space-m));
}

/* Tighten only the matrix (+ legend) → task-controls gap; the panel's uniform
   space-m is too airy right there. */
.settings-panel :deep(.benchmark-task) {
    margin-top: calc(var(--wa-space-3xs) - var(--wa-space-m));
}

.settings-info-callout,
.startup-warning-callout,
.idle-warning-callout,
.model-fallback-callout,
.provider-blocked-callout,
.hybrid-settings-note {
    font-size: var(--wa-font-size-s);
    width: 100%;
}

.model-fallback-callout {
    margin-bottom: 0.4rem;
}

.provider-blocked-callout {
    flex-shrink: 0;
    margin-bottom: var(--wa-space-l);

    /* The "Remove …" affordance rides inline with the explanatory
       sentence rather than dropping onto its own line — overrides the
       default block-flex layout the shared link class carries elsewhere. */
    .settings-action-link {
        display: inline;
        color: inherit;
        text-decoration: underline;
    }
}

.settings-panel-presets {
    /* Single full-width control now that the provider selector moved into the
       matrix — the Reset / Presets dropdown spans the popover width. */
    display: block;
    flex-shrink: 0;
    padding-bottom: var(--wa-space-s);
    wa-dropdown::part(menu) {
        max-width: 90vw !important;
        width: auto;
    }
}

.presets-dropdown {
    display: block;
    width: 100%;
}

.presets-trigger {
    width: 100%;
}

.presets-trigger::part(base) {
    width: 100%;
    justify-content: space-between;
}

.presets-trigger-label {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

/* Provider group headers are disabled dropdown-items (non-selectable only);
   undo wa-dropdown-item's disabled dimming (opacity: 0.5) and pin the provider
   name to the normal text color so it reads clearly. */
.group-header {
    opacity: 1 !important;
}
.group-header::part(label) {
    color: var(--wa-color-text-normal);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: var(--wa-font-size-xs);
}

.settings-panel-actions {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
    padding-bottom: var(--wa-space-s);
    border-bottom: 1px solid var(--wa-color-border);
}

.settings-panel-links {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs) var(--wa-space-s);
    justify-content: center;
}

.settings-action-link {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-brand-60);
    cursor: pointer;
    text-decoration: none;
    display: flex;
    align-items: center;
    gap: var(--wa-space-3xs);
    &:hover {
        text-decoration: underline;
    }
}

.setting-row {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.setting-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.setting-help {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.select-group-label {
    display: block;
    padding: var(--wa-space-3xs) var(--wa-space-l);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.reset-setting-link {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-60);
    cursor: pointer;
    text-decoration: none;
    &:hover {
        text-decoration: underline;
    }
}

/* Plain text descriptions need block; the agent-settings summary carries this
   class too but brings its own flex display, so don't force block on it. */
.option-description:not(.agent-settings-summary) {
    display: block;
}
.option-description {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.permission-option-icon {
    margin-right: 0.5em;
    vertical-align: -0.15em;
}

.preset-item::part(label),
.reset-item::part(label) {
    white-space: normal;
    max-width: 25rem;
}
</style>
