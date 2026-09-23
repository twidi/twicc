<script setup>
/**
 * ProviderSettingsSection — the per-provider section of the Settings popover
 * (one per registered provider), editing that provider's persisted agent-
 * settings defaults.
 *
 * Adopts the per-session agent-settings design: a provider × model × effort
 * matrix (owns the default model + effort) with its benchmark-score task
 * controls, a compact row of switches (context toggle, thinking, Chrome MCP,
 * fast mode), and permission as plain wa-selects. The matrix, task controls, switches
 * and their builders are SHARED with ``AgentSettingsPopover`` — see
 * ``utils/agentMatrix.js`` / ``utils/agentSwitchRows.js`` and the three
 * ``AgentSettings*`` components under ``components/message/``.
 *
 * Unlike the session popover, the values here are the persisted ``defaults``
 * (never the null "follow default" sentinel): every write goes straight to the
 * provider store via ``getDefaultValue`` / ``setDefaultValue``, no Apply step.
 * Only the fields a provider supports (per ``supportsAgentSetting`` /
 * ``fieldHasChoice``) render — so Codex shows just the matrix + permission
 * selects (its context is model-fixed, and it has no thinking/Chrome/fast).
 */
import { computed, ref } from 'vue'
import { getProviderHelpers, getProviderIcon } from '../../providers'
import ProviderIcon from '../ui/ProviderIcon.vue'
import PermissionModeIcon from '../ui/PermissionModeIcon.vue'
import { useSettingsStore } from '../../stores/settings'
import AgentSettingsPresetsDialog from './AgentSettingsPresetsDialog.vue'
import AgentSettingsDefaultsPicker from '../message/AgentSettingsDefaultsPicker.vue'
import HelpTextLink from '../help/HelpTextLink.vue'
import HybridModeExplainer from '../message/HybridModeExplainer.vue'

const props = defineProps({
    provider: {
        type: String,
        required: true,
    },
})

const helpers = computed(() => getProviderHelpers(props.provider))
const providerIcon = computed(() => getProviderIcon(props.provider))

// Permission selects rendered below the switches. Concrete defaults (no
// sentinel); ``permission_mode_if_untrusted`` is the default-shaping pseudo-
// field used when a project resolves untrusted (trust design §13.1).
const PERMISSION_FIELDS = ['permission_mode', 'permission_mode_if_untrusted']

// Section-specific labels — kept distinct from the generic ``getFieldLabel`` so
// the existing UI strings stay stable ("Default permission mode", …).
const PERMISSION_LABELS = {
    permission_mode: 'Default permission mode',
    permission_mode_if_untrusted: 'Default permission mode (untrusted projects)',
}

// ─── Matrix + task controls + switches (shared AgentSettingsDefaultsPicker) ──
// Values ARE the persisted defaults, so the picker reads them raw (no null
// layer) and never shows a separate default dot — the selection IS the default.
function defaultValueFor(field) {
    return helpers.value.getDefaultValue(field)
}

function onMatrixSelect({ model, effort }) {
    // Set the model first: the provider setter re-runs consistency (cascading
    // context/effort/fast/permission/thinking against the new model), then the
    // explicit effort write applies the clicked (guaranteed-enabled) cell.
    helpers.value.setDefaultValue('selected_model', model)
    helpers.value.setDefaultValue('effort', effort)
}

function onSwitchChange({ field, value }) {
    helpers.value.setDefaultValue(field, value)
}

// Render context fed to the permission hooks. Out of a session, the only gating
// comes from the default model's capabilities.
function fieldContext(field) {
    return {
        field,
        effectiveModel: helpers.value.resolveToAvailableModel(helpers.value.getDefaultValue('selected_model')),
        selectedValue: helpers.value.getDefaultValue(field),
        defaultValue: helpers.value.getDefaultValue(field),
    }
}

// ─── Permission selects (concrete defaults, string-valued) ─────────────────
const permissionRows = computed(() => {
    const h = helpers.value
    return PERMISSION_FIELDS.filter(field => h.supportsAgentSetting(field)).map(field => {
        const ctx = fieldContext(field)
        const value = h.getDefaultValue(field)
        return {
            field,
            label: PERMISSION_LABELS[field],
            value: value === null || value === undefined ? '' : String(value),
            helpText: h.getFieldHelpText(field, ctx),
            // Concrete default → glyph for the closed select.
            iconInfo: h.getChoiceIcon(field, value),
            choices: h.getFieldChoices(field).map(opt => {
                const disabled = h.isChoiceDisabled(field, opt.value, ctx)
                const disabledReason = disabled ? h.getChoiceDisabledReason(field, opt.value, ctx) : null
                return {
                    value: String(opt.value),
                    labelWithSuffix: disabled ? `${opt.label} (not available)` : opt.label,
                    description: disabledReason ?? opt.description ?? null,
                    disabled,
                    icon: opt.icon ?? null,
                    color: opt.color ?? null,
                }
            }),
        }
    })
})

// Permission modes are string-valued, so the raw wa-select value is written
// straight through (no boolean/int coercion needed — those fields are switches).
function onPermissionChange(field, event) {
    helpers.value.setDefaultValue(field, event.target.value)
}

const presetsDialogOpen = ref(false)
function openPresetsDialog() {
    presetsDialogOpen.value = true
}

// --- Orchestration opt-out (soft preference, synced) -----------------------
// Whether agents may pick this provider on their own when orchestrating
// sessions. Soft: an explicit user request for the provider still works.
const settingsStore = useSettingsStore()

const orchestrationEnabled = computed(
    () => !(settingsStore.orchestrationDisabledProviders || []).includes(props.provider),
)

// The last orchestration-enabled provider cannot be switched off — the
// orchestration pool must never end up empty from the UI.
const isLastOrchestrationProvider = computed(
    () => orchestrationEnabled.value && settingsStore.orchestrationProviders.length <= 1,
)

function onOrchestrationToggle(event) {
    const enabled = event.target.checked
    const current = new Set(settingsStore.orchestrationDisabledProviders || [])
    if (enabled) {
        current.delete(props.provider)
    } else {
        if (isLastOrchestrationProvider.value) {
            // Race guard (e.g. stale UI before a WS resync): revert the switch.
            event.target.checked = true
            return
        }
        current.add(props.provider)
    }
    settingsStore.orchestrationDisabledProviders = [...current].sort()
}
</script>

<template>
    <section class="settings-section">
        <h3 class="settings-section-title">
            <ProviderIcon v-if="providerIcon" :provider="provider" />
            {{ helpers.constructor.label }} settings
            <wa-icon name="cloud" class="synced-icon"></wa-icon>
        </h3>

        <!-- Hybrid mode default (Claude Code only): new sessions start in hybrid
             mode. Drafts only — existing sessions are never enforced. Hidden while
             the hybrid feature flag is off (the whole feature is dormant). -->
        <div v-if="provider === 'claude_code' && settingsStore.isClaudeHybridEnabled" class="setting-group">
            <label class="setting-group-label">Hybrid Mode</label>
            <wa-switch
                :checked="settingsStore.isClaudeHybridDefault"
                @change="settingsStore.setClaudeHybridDefault($event.target.checked)"
            >Activate on new sessions</wa-switch>
            <HybridModeExplainer />
        </div>
        <wa-divider v-if="provider === 'claude_code' && settingsStore.isClaudeHybridEnabled"></wa-divider>

        <!-- Default model × effort matrix (+ score task controls) and the switch row.
             Own wrapper class (not .setting-group) so the section's
             `label ~ :not(label)` indent rule doesn't shift the wide grid. -->
        <div class="agent-defaults-group">
            <div class="matrix-heading">
                <label class="setting-group-label">Model &amp; effort picker</label>
                <HelpTextLink help-key="model-effort-score" label="What are those numbers?" />
            </div>
            <AgentSettingsDefaultsPicker
                :provider="provider"
                :value-for="defaultValueFor"
                @select="onMatrixSelect"
                @change="onSwitchChange"
            />
        </div>

        <!-- No divider here: the task-controls block ends with its own trailing divider
             (and the switches, when present, follow it) — mirroring the popover,
             where permission follows the switches with no extra rule. This also
             avoids a double divider on Codex, whose switch row is empty. -->

        <!-- Permission defaults — plain selects (concrete defaults, no sentinel). -->
        <div
            v-for="row in permissionRows"
            :key="row.field"
            class="setting-group"
        >
            <label class="setting-group-label">{{ row.label }}</label>
            <wa-select
                :value.prop="row.value"
                size="small"
                @change="onPermissionChange(row.field, $event)"
            >
                <PermissionModeIcon
                    v-if="row.iconInfo"
                    slot="start"
                    :icon="row.iconInfo.icon"
                    :color="row.iconInfo.color"
                />
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
            <span v-if="row.helpText" class="setting-group-hint">{{ row.helpText }}</span>
        </div>

        <wa-divider></wa-divider>

        <div class="setting-group">
            <label class="setting-group-label">Presets</label>
            <span class="setting-group-hint">
                Define bundles of {{ helpers.constructor.label }} settings to quickly apply to a session
            </span>
            <wa-button size="small" @click="openPresetsDialog">
                <wa-icon slot="start" name="sliders"></wa-icon>
                Manage presets…
            </wa-button>
        </div>

        <wa-divider></wa-divider>

        <div class="setting-group">
            <label class="setting-group-label">Orchestration</label>
            <wa-switch
                :checked="orchestrationEnabled"
                :disabled="isLastOrchestrationProvider"
                @change="onOrchestrationToggle"
            >Enabled in orchestration</wa-switch>
            <span class="setting-group-hint">
                When off, agents won't pick {{ helpers.constructor.label }} on their own when
                orchestrating sessions. You can still explicitly ask an agent to use it.
            </span>
            <span v-if="isLastOrchestrationProvider" class="setting-group-hint">
                At least one provider must remain enabled in orchestration.
            </span>
        </div>

        <AgentSettingsPresetsDialog
            v-model:open="presetsDialogOpen"
            :provider="provider"
        />
    </section>
</template>

<style scoped>
.permission-option-icon {
    margin-right: 0.5em;
    vertical-align: -0.15em;
}

.option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.synced-icon {
    color: var(--wa-color-brand);
}

/* Heading + picker stack. A dedicated class (not .setting-group) so the
   section's `.setting-group > label ~ :not(label)` indent rule never shifts
   the wide matrix grid; the label still gets its styling from the class-keyed
   `.settings-sections .setting-group-label` rule. The callout / matrix /
   task controls / switches layout lives in AgentSettingsDefaultsPicker. */
.agent-defaults-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

/* Heading above the matrix ("Model & effort picker" + help link). Pull the
   picker up to a tight gap under the label. */
.matrix-heading {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    column-gap: var(--wa-space-s);
    row-gap: var(--wa-space-3xs);
    margin-bottom: calc(var(--wa-space-2xs) - var(--wa-space-m));
}
</style>
