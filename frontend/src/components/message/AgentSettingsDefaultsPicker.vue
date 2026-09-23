<script setup>
// Single-provider agent-settings picker built on the shared matrix design: the
// unavailable-model fallback callout, the model × effort matrix, the
// benchmark-score task controls (auto-select hidden — the benchmarkTask store
// is global and the always-mounted message popover watches it), and the compact
// switch row. Shared by the Settings panel's per-provider defaults editor
// (values = the persisted defaults; no default dot, the selection IS the
// default) and the preset editor (values = the preset's nullable overrides; the
// provider-default cell keeps its dot).
//
// The host owns the values: ``valueFor(field)`` returns the raw stored value
// (``null`` = follow the provider default); displayed state is the effective
// value (raw ?? default), with the (model, effort) pair reconciled through the
// provider's consistency rules so the matrix never highlights a disabled cell.
// Writes flow back through the ``select`` (matrix pick) and ``change`` (switch)
// events — this component never writes anywhere itself.
import { computed } from 'vue'
import { getProviderHelpers } from '../../providers'
import { useBenchmarksStore } from '../../stores/benchmarks'
import { buildEffortColumns, buildMatrixBlocks } from '../../utils/agentMatrix'
import { buildSwitchRows } from '../../utils/agentSwitchRows'
import AgentSettingsMatrix from './AgentSettingsMatrix.vue'
import AgentSettingsBenchmarkTask from './AgentSettingsBenchmarkTask.vue'
import AgentSettingsSwitches from './AgentSettingsSwitches.vue'

const props = defineProps({
    provider: { type: String, required: true },
    // (field) => raw stored value; null/undefined = follow the provider default.
    valueFor: { type: Function, required: true },
    // Mark the provider's default (model, effort) cell with the filled dot.
    showDefaultDot: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'change'])

const helpers = computed(() => getProviderHelpers(props.provider))
const benchmarksStore = useBenchmarksStore()

function effective(field) {
    return props.valueFor(field) ?? helpers.value.getDefaultValue(field)
}

// The highlighted (model, effort) pair, reconciled through the provider's
// consistency rules: a retired/disabled stored model resolves to its available
// substitute and the effort demotes to what that model supports.
const effectivePair = computed(() => helpers.value.enforceAgentSettingsConsistency({
    selectedModel: effective('selected_model'),
    effort: effective('effort'),
}))

// The provider's own default cell (same reconciliation), dotted when shown.
const defaultCell = computed(() => {
    if (!props.showDefaultDot) return null
    const h = helpers.value
    const d = h.enforceAgentSettingsConsistency({
        selectedModel: h.getDefaultValue('selected_model'),
        effort: h.getDefaultValue('effort'),
    })
    return { provider: props.provider, model: d.selectedModel, effort: d.effort }
})

const effortColumns = computed(() => buildEffortColumns([props.provider]))
const matrixBlocks = computed(() => buildMatrixBlocks({
    providers: [props.provider],
    effortColumns: effortColumns.value,
    currentProvider: props.provider,
    selectedModel: effectivePair.value.selectedModel,
    selectedEffort: effectivePair.value.effort,
    defaultCell: defaultCell.value,
    benchmarksStore,
}))

const modelFallbackNotice = computed(() =>
    helpers.value?.getModelFallbackNotice(props.valueFor('selected_model')) ?? null,
)

// Render context fed to the switch hooks. Out of a session, the only gating
// comes from the effective model's capabilities (context/thinking/fast).
function fieldContext(field) {
    return {
        field,
        effectiveModel: effectivePair.value.selectedModel,
        selectedValue: props.valueFor(field),
        defaultValue: helpers.value.getDefaultValue(field),
    }
}

const switchRows = computed(() => buildSwitchRows(helpers.value, {
    fieldContext,
    valueFor: effective,
}))

function onMatrixSelect({ model, effort }) {
    emit('select', { model, effort })
}
</script>

<template>
    <div class="defaults-picker">
        <wa-callout
            v-if="modelFallbackNotice"
            variant="warning"
            class="model-fallback-callout"
        >
            {{ modelFallbackNotice }}
        </wa-callout>
        <AgentSettingsMatrix
            :blocks="matrixBlocks"
            :effort-columns="effortColumns"
            @select="onMatrixSelect"
        />
        <AgentSettingsBenchmarkTask :provider-count="1" :show-auto-select="false" />
        <AgentSettingsSwitches :rows="switchRows" @change="emit('change', $event)" />
    </div>
</template>

<style scoped>
.defaults-picker {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

/* Pull the task controls up toward the matrix (the uniform gap is a touch
   airy right there), matching the popover's tightened matrix→task-controls gap. */
.defaults-picker :deep(.benchmark-task) {
    margin-top: calc(var(--wa-space-2xs) - var(--wa-space-m));
}

.model-fallback-callout {
    display: block;
    font-size: var(--wa-font-size-s);
}
</style>
