<script setup>
// Model & effort picker with two modes, shared by every surface that picks them
// (the session popover, the Settings defaults editor, the preset editor):
// - simple (default): provider (only when several are offered), model (a slider
//   from the weakest to the strongest latest model, or a select of every model
//   once older ones are shown) and effort (a slider over the model's efforts);
// - matrix: the score matrix with its task controls (task type / difficulty /
//   favor / auto-select).
// Both read the same ``blocks`` (utils/agentMatrix.js) and emit the same
// ``select`` ({ provider, model, effort }), so the selection survives a switch
// of mode. The mode lives in the benchmarkTask store (``pickerMode``); the
// switch itself sits in the heading (MatrixHeadingLinks).
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useBenchmarkTaskStore } from '../../stores/benchmarkTask'
import { effortIconSrc } from '../../utils/effortIcon'
import {
    currentBlock, selectedRow, selectedEffort, sliderRows, enabledEfforts,
    effortForModel, providerStartSelection,
} from '../../utils/agentSimplePicker'
import SegmentedControl from '../ui/SegmentedControl.vue'
import ProviderIcon from '../ui/ProviderIcon.vue'
import AgentSettingsMatrix from './AgentSettingsMatrix.vue'
import AgentSettingsBenchmarkTask from './AgentSettingsBenchmarkTask.vue'

const props = defineProps({
    // Same shapes as AgentSettingsMatrix.
    blocks: { type: Array, default: () => [] },
    effortColumns: { type: Array, default: () => [] },
    // Forwarded to AgentSettingsBenchmarkTask (matrix mode).
    showAutoSelect: { type: Boolean, default: true },
})

const emit = defineEmits(['select'])

const taskStore = useBenchmarkTaskStore()
const isMatrix = computed(() => taskStore.pickerMode === 'matrix')

const block = computed(() => currentBlock(props.blocks))
const row = computed(() => selectedRow(block.value))
const effort = computed(() => selectedEffort(row.value))

// ─── Provider ────────────────────────────────────────────────────────────
// No ``icon`` key: SegmentedControl would draw it as a second, empty-family icon.
// The provider icon goes through the ``option-start-<value>`` slot instead.
const providerOptions = computed(() => props.blocks.map(b => ({ value: b.provider, label: b.label, hasIcon: !!b.icon })))

function onProviderChange(provider) {
    if (provider === block.value?.provider) return
    const start = providerStartSelection(props.blocks.find(b => b.provider === provider))
    if (start) emit('select', { provider, ...start })
}

// ─── Model ───────────────────────────────────────────────────────────────
const hasOldModels = computed(() => !!block.value?.rows.some(r => !r.isLatest))
const selectedIsOld = computed(() => !!row.value && !row.value.isLatest)
// A selected older model puts the picker in "older models" mode, and it stays
// there until a latest model is picked: no slider position stands for it.
const olderShown = computed(() => taskStore.showOlder || selectedIsOld.value)

const modelRows = computed(() => sliderRows(block.value))
const modelIndex = computed(() => Math.max(0, modelRows.value.findIndex(r => r.model === row.value?.model)))

function modelLabel(r) {
    return [r.name, r.version].filter(Boolean).join(' ')
}

function pickModel(target) {
    if (!target) return
    const next = effortForModel(target, props.effortColumns, effort.value)
    if (next == null) return
    emit('select', { provider: block.value.provider, model: target.model, effort: next })
}

function onModelSlider(event) {
    pickModel(modelRows.value[Math.round(Number(event.target.value))])
}

function onModelSelect(event) {
    pickModel(block.value.rows.find(r => r.model === event.target.value))
}

// ─── Effort ──────────────────────────────────────────────────────────────
const effortItems = computed(() => {
    const supported = enabledEfforts(row.value)
    return props.effortColumns.filter(c => supported.includes(c.effort))
})
const effortIndex = computed(() => Math.max(0, effortItems.value.findIndex(c => c.effort === effort.value)))

function pickEffort(next) {
    if (next === undefined || next === effort.value) return
    emit('select', { provider: block.value.provider, model: row.value.model, effort: next })
}

function onEffortSlider(event) {
    pickEffort(effortItems.value[Math.round(Number(event.target.value))]?.effort)
}

// Left offset (as a CSS length) of the label of item ``i`` out of ``n``, so it
// sits under its slider position (the thumb centre runs from half a thumb to
// the track end minus half a thumb), plus the matching alignment shift: the
// first label starts at the slider's start, the last one ends at its end.
function labelStyle(i, n) {
    const ratio = n > 1 ? i / (n - 1) : 0
    return {
        left: `calc(var(--thumb-half) + (100% - 2 * var(--thumb-half)) * ${ratio})`,
        transform: `translateX(-${ratio * 100}%)`,
    }
}

function toggleOlder() {
    taskStore.showOlder = !taskStore.showOlder
}

// ─── Label layout ────────────────────────────────────────────────────────
// A label is made of two parts (model: name + version; effort: icon + text).
// They sit side by side while every label of the row fits in the room between
// two neighbouring slider positions, else ALL the labels of that row stack
// their parts (second part under the first). The side-by-side width is read
// from the parts themselves, so it does not depend on the current layout.
const PART_GAP = 4 // px, the gap between the two parts of a side-by-side label
const LABEL_MARGIN = 8 // px kept free between two neighbouring labels

function labelsFit(container) {
    const labels = [...container.querySelectorAll('.slider-label')]
    if (labels.length < 2) return true
    const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
    const room = (container.clientWidth - rootPx) / (labels.length - 1) // thumb is 1rem wide
    const widest = Math.max(...labels.map(label => {
        const parts = [...label.querySelectorAll('.label-part')]
        return parts.reduce((sum, part) => sum + part.offsetWidth, 0) + PART_GAP * (parts.length - 1)
    }))
    return widest + LABEL_MARGIN <= room
}

// Tracks one labels row: re-measures when its box resizes or ``source`` changes.
function useStackedLabels(containerRef, source) {
    const stacked = ref(false)
    let observer = null
    function measure() {
        const el = containerRef.value
        // Hidden (closed popover): no width to judge by, keep the last answer.
        if (!el || !el.clientWidth) return
        stacked.value = !labelsFit(el)
    }
    watch(containerRef, (el, old) => {
        if (old) observer?.unobserve(old)
        if (el) {
            observer ??= new ResizeObserver(measure)
            observer.observe(el)
            measure()
        }
    }, { flush: 'post' })
    watch(source, () => nextTick(measure), { flush: 'post' })
    onBeforeUnmount(() => observer?.disconnect())
    return stacked
}

const modelLabelsRef = ref(null)
const effortLabelsRef = ref(null)
const modelStacked = useStackedLabels(modelLabelsRef, modelRows)
const effortStacked = useStackedLabels(effortLabelsRef, effortItems)

// Older models only matter to the matrix scores; the simple picker has no
// scores, so a leftover "Show older models" would only be a surprise there.
watch(() => taskStore.pickerMode, (mode) => {
    if (mode === 'simple' && !selectedIsOld.value) taskStore.showOlder = false
})
</script>

<template>
    <!-- Own column with the host's gap, so the task controls keep their tightened
         spacing under the matrix; the top margin sets it off from the links row
         above. -->
    <div v-if="isMatrix" class="matrix-mode">
        <AgentSettingsMatrix
            :blocks="blocks"
            :effort-columns="effortColumns"
            @select="emit('select', $event)"
        />
        <AgentSettingsBenchmarkTask :provider-count="blocks.length" :show-auto-select="showAutoSelect" />
    </div>

    <div v-else-if="block" class="simple-picker">
        <template v-if="blocks.length > 1">
            <span class="simple-label">Provider</span>
            <SegmentedControl
                class="simple-provider"
                label="Provider"
                :model-value="block.provider"
                :options="providerOptions"
                @update:model-value="onProviderChange"
            >
                <template v-for="o in providerOptions" :key="o.value" #[`option-start-${o.value}`]>
                    <ProviderIcon v-if="o.hasIcon" :provider="o.value" class="simple-provider-icon" />
                </template>
            </SegmentedControl>
            <div class="simple-gap"></div>
        </template>

        <span class="simple-label">Model</span>
        <wa-select
            v-if="olderShown"
            size="small"
            aria-label="Model"
            :value.prop="row?.model ?? ''"
            @change="onModelSelect"
        >
            <wa-option v-for="r in block.rows" :key="r.model" :value="r.model" :disabled="!r.cells.some(c => c.enabled)">
                {{ modelLabel(r) }}
            </wa-option>
        </wa-select>
        <wa-slider
            v-else
            class="simple-slider"
            size="small"
            with-markers
            aria-label="Model, from the weakest to the strongest"
            :min.prop="0"
            :max.prop="Math.max(0, modelRows.length - 1)"
            :step.prop="1"
            :disabled.prop="modelRows.length < 2"
            :value.prop="modelIndex"
            @input="onModelSlider"
        ></wa-slider>
        <div class="simple-below">
            <div v-if="!olderShown" ref="modelLabelsRef" class="slider-labels" :class="{ stacked: modelStacked }">
                <button
                    v-for="(r, i) in modelRows"
                    :key="r.model"
                    type="button"
                    class="slider-label"
                    :class="{ active: r.model === row?.model }"
                    :style="labelStyle(i, modelRows.length)"
                    @click="pickModel(r)"
                >
                    <span class="label-part">{{ r.name }}</span>
                    <span v-if="r.version" class="label-part">{{ r.version }}</span>
                </button>
            </div>
            <button
                v-if="hasOldModels && !selectedIsOld"
                type="button"
                class="picker-link"
                @click="toggleOlder"
            >{{ olderShown ? 'Hide older models' : 'Show older models' }}</button>
        </div>
        <div class="simple-gap"></div>

        <span class="simple-label">Effort</span>
        <wa-slider
            class="simple-slider"
            size="small"
            with-markers
            aria-label="Effort"
            :min.prop="0"
            :max.prop="Math.max(0, effortItems.length - 1)"
            :step.prop="1"
            :disabled.prop="effortItems.length < 2"
            :value.prop="effortIndex"
            @input="onEffortSlider"
        ></wa-slider>
        <div class="simple-below">
            <div ref="effortLabelsRef" class="slider-labels" :class="{ stacked: effortStacked }">
                <button
                    v-for="(c, i) in effortItems"
                    :key="c.effort"
                    type="button"
                    class="slider-label"
                    :class="{ active: c.effort === effort }"
                    :style="labelStyle(i, effortItems.length)"
                    @click="pickEffort(c.effort)"
                >
                    <wa-icon v-if="effortIconSrc(c.effort)" auto-width :src="effortIconSrc(c.effort)" class="label-part slider-label-icon"></wa-icon>
                    <span class="label-part">{{ c.label }}</span>
                </button>
            </div>
        </div>

        <wa-divider class="simple-divider"></wa-divider>
    </div>
</template>

<style scoped>
.picker-link {
    align-self: flex-start;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-60);
    &:hover {
        text-decoration: underline;
    }
}

/* The host's heading pulls what follows it up (negative bottom margin): the
   top margins give the picker back some room under the heading. */
.matrix-mode {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    margin-top: var(--wa-space-s);
}

/* Labels in the first column, controls in the second (like the task controls),
   each label centred on its control. The labels under a slider, and the groups'
   spacing, are rows of their own. */
.simple-picker {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    column-gap: var(--wa-space-xs);
    margin-top: var(--wa-space-s);
}

.simple-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    white-space: nowrap;
}

.simple-gap {
    grid-column: 1 / -1;
    height: var(--wa-space-s);
}

.simple-below {
    grid-column: 2;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
    min-width: 0;
}

.simple-provider {
    justify-self: start;
}

.simple-slider {
    --thumb-width: 1rem;
    --thumb-height: 1rem;
    --track-size: 0.5rem;
}

/* Same native-stylesheet icon margin as above: set it explicitly. */
.simple-provider-icon {
    margin-inline: 0 var(--wa-space-2xs) !important;
}

/* Labels sit under the slider, each one at its own position (absolute, shifted
   by labelStyle). --thumb-half is half the thumb width set on the slider. */
.slider-labels {
    --thumb-half: 0.5rem;
    position: relative;
    height: 1.4rem;
    &.stacked {
        height: 2.6rem;
    }
}

.slider-label {
    position: absolute;
    top: 0;
    /* Web Awesome's native stylesheet gives every <button> a form-control height. */
    height: auto;
    min-height: 0;
    display: inline-flex;
    /* Side by side: the icon's bottom sits on the text's baseline. */
    align-items: baseline;
    gap: 4px; /* PART_GAP */
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    white-space: nowrap;
    font-size: var(--wa-font-size-xs);
    line-height: 1.2;
    color: var(--wa-color-text-quiet);
    &.active {
        color: var(--wa-color-text-normal);
        font-weight: var(--wa-font-weight-semibold);
    }
    &:hover {
        color: var(--wa-color-text-normal);
    }
}

/* Too narrow for side-by-side parts: the second part goes under the first. */
.stacked .slider-label {
    flex-direction: column;
    align-items: center;
    gap: 0;
}

/* The box hugs the bars (31 × 24 viewBox). Web Awesome's native stylesheet puts
   a 0.75em end margin on every icon followed by a sibling inside a <button>;
   the label's own gap replaces it. */
.slider-label-icon {
    font-size: 1.2em;
    flex: none;
    margin: 0 !important;
}

/* Closes the block, like the matrix's task controls. */
.simple-divider {
    grid-column: 1 / -1;
    margin: var(--wa-space-s) 0 0;
}
</style>
