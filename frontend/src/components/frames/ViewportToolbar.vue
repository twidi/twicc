<script setup>
// Sub-toolbar of the responsive-viewport mode, shared by the Browser pane and
// the artifact HTML preview. Preset list, manual dimension fields and the
// swap button all drive the same width/height pair the stage's drag handles
// update — everything stays in sync whichever way the size changes.
import { computed, useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'
import { VIEWPORT_MAX, VIEWPORT_MIN, VIEWPORT_PRESETS, clampViewportSize } from './viewport'

const props = defineProps({
    width: { type: Number, required: true },
    height: { type: Number, required: true },
})
const emit = defineEmits(['update:width', 'update:height', 'close'])

const instanceId = useId()

// The preset matching the current dimensions in either orientation (a rotated
// device is still that device); '' → the select shows its "Custom" placeholder.
const presetValue = computed(() => {
    const match = VIEWPORT_PRESETS.find(
        (p) =>
            (p.width === props.width && p.height === props.height) ||
            (p.width === props.height && p.height === props.width)
    )
    return match ? `${match.width}x${match.height}` : ''
})

function onPresetChange(event) {
    const match = VIEWPORT_PRESETS.find((p) => `${p.width}x${p.height}` === event.target.value)
    if (!match) return
    emit('update:width', match.width)
    emit('update:height', match.height)
}

function onSizeChange(axis, event) {
    const current = axis === 'w' ? props.width : props.height
    const parsed = Number.parseInt(event.target.value, 10)
    const applied = Number.isFinite(parsed) ? clampViewportSize(parsed) : current
    if (applied !== current) emit(axis === 'w' ? 'update:width' : 'update:height', applied)
    // Reflect the applied value back into the field (covers clamping and
    // garbage input, which leave the binding unchanged).
    event.target.value = String(applied)
}

function swap() {
    emit('update:width', props.height)
    emit('update:height', props.width)
}
</script>

<template>
    <div class="viewport-toolbar">
        <span class="subbar-label">Viewport</span>
        <wa-select
            class="viewport-preset reduced-height"
            size="small"
            placeholder="Custom size"
            :value="presetValue"
            @change="onPresetChange"
        >
            <wa-option
                v-for="preset in VIEWPORT_PRESETS"
                :key="preset.label"
                :value="`${preset.width}x${preset.height}`"
            >{{ preset.label }} — {{ preset.width }}×{{ preset.height }}</wa-option>
        </wa-select>
        <div class="viewport-dimensions">
            <wa-input
                class="viewport-size-input reduced-height"
                size="small"
                type="number"
                :min="VIEWPORT_MIN"
                :max="VIEWPORT_MAX"
                :value="String(width)"
                @change="onSizeChange('w', $event)"
            ></wa-input>
            <span class="viewport-glue">×</span>
            <wa-input
                class="viewport-size-input reduced-height"
                size="small"
                type="number"
                :min="VIEWPORT_MIN"
                :max="VIEWPORT_MAX"
                :value="String(height)"
                @change="onSizeChange('h', $event)"
            ></wa-input>
            <span class="viewport-glue">px</span>
            <wa-button :id="`viewport-swap-${instanceId}`" appearance="plain" size="small" class="subbar-btn reduced-height" @click="swap">
                <wa-icon name="right-left"></wa-icon>
            </wa-button>
            <AppTooltip :for="`viewport-swap-${instanceId}`">Swap width and height</AppTooltip>
        </div>
        <wa-button :id="`viewport-close-${instanceId}`" appearance="plain" size="small" class="subbar-btn reduced-height subbar-close" @click="$emit('close')">
            <wa-icon name="xmark"></wa-icon>
        </wa-button>
        <AppTooltip :for="`viewport-close-${instanceId}`">Exit responsive mode</AppTooltip>
    </div>
</template>

<style scoped>
.viewport-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs);
    border-bottom: 1px solid var(--wa-color-border-quiet);
    flex-shrink: 0;
}

.subbar-label {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    padding-inline: var(--wa-space-2xs);
}

.subbar-btn {
    flex-shrink: 0;
}

/* Exit button pinned to the far right, away from the mode's controls. */
.subbar-close {
    margin-left: auto;
}

.viewport-preset {
    flex: 1;
    min-width: min(5rem, 50%);
}

.viewport-dimensions {
    display: flex;
    align-items: center;
    gap: var(--wa-space-3xs);
}

.viewport-size-input {
    width: 5rem;
}

/* reduced-height shrinks the whole control through its font-size; the typed
   dimension (native input part) and the select's displayed value get the
   small size back. */
.viewport-size-input::part(input),
.viewport-preset::part(display-input) {
    font-size: var(--wa-font-size-s);
}

/* The "×" between the fields and the trailing "px" unit. */
.viewport-glue {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}
</style>
