<script setup>
/**
 * SegmentedControl - A single choice drawn as a row of segments in one bordered frame;
 * the checked segment's fill glides from segment to segment (visual refresh step 4c,
 * docs/plans/2026-09-28-gliding-indicators-design.md §7).
 *
 * It wraps a <wa-radio-group> of button radios, so keyboard (arrows), focus and form
 * semantics stay Web Awesome's. The group's label is its accessible name only: the
 * caller shows its own visible label.
 *
 * Usage:
 *   <SegmentedControl label="Favor" v-model="favor"
 *       :options="[{ value: 'cost', label: 'Cost' }, { value: 'speed', label: 'Speed', icon: 'bolt' }]" />
 */
import { ref } from 'vue'
import { useGlideInk } from '../../composables/useGlideInk'

const props = defineProps({
    modelValue: { type: String, required: true },
    options: { type: Array, required: true }, // [{ value: String, label: String, icon?: String }]
    label: { type: String, required: true }, // accessible name of the radiogroup (not shown)
    size: { type: String, default: 'small' },
})

const emit = defineEmits(['update:modelValue'])

const frameRef = ref(null)
const inkRef = ref(null)

function onChange(event) {
    // A radio's own change bubbles here too: only the group's counts.
    if (event.target !== event.currentTarget) return
    emit('update:modelValue', event.target.value)
}

useGlideInk({
    container: frameRef,
    flushTarget: inkRef,
    // The `value` property: Vue sets it as a property, Web Awesome reflects it to the
    // attribute only in its own later update, after Vue's post-flush watchers.
    getActive: () => [...(frameRef.value?.querySelectorAll('wa-radio') ?? [])]
        .find((radio) => radio.value === props.modelValue) ?? null,
    getItems: () => [...(frameRef.value?.querySelectorAll('wa-radio') ?? [])],
    sources: [() => props.modelValue, () => props.options],
})
</script>

<template>
    <div ref="frameRef" class="segmented-control">
        <span ref="inkRef" class="glide-ink" aria-hidden="true"></span>
        <wa-radio-group
            :label="label"
            :size="size"
            orientation="horizontal"
            :value.prop="modelValue"
            @change="onChange"
        >
            <wa-radio v-for="option in options" :key="option.value" appearance="button" :value="option.value">
                <wa-icon v-if="option.icon" :name="option.icon" class="segmented-icon"></wa-icon>
                {{ option.label }}
            </wa-radio>
        </wa-radio-group>
    </div>
</template>

<style scoped>
.segmented-control {
    --segmented-pad: 0.1875rem;
    --glide-ink-bg: var(--wa-color-brand-fill-normal);
    --glide-ink-radius: var(--wa-border-radius-s);
    position: relative;
    display: inline-flex;
    padding: var(--segmented-pad);
    border: var(--wa-form-control-border-width) var(--wa-form-control-border-style) var(--wa-form-control-border-color);
    border-radius: var(--wa-border-radius-m);
    background: var(--wa-form-control-background-color);
}

wa-radio-group {
    position: relative; /* paints above the ink, tree order */
}

wa-radio-group::part(form-control-label) {
    /* the accessible name stays; the caller shows its own visible label */
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
}

wa-radio-group::part(form-control-input) {
    gap: var(--segmented-pad);
}

/* A document rule on the host beats Web Awesome's :host(...) rules: the checked fill,
   the joined-button radii and the overlap margins are replaced, so a segment's box never
   depends on its checked state. */
wa-radio[appearance='button'] {
    margin: 0;
    border-color: transparent;
    border-radius: var(--glide-ink-radius);
    background-color: transparent;
    color: var(--wa-color-text-quiet);
    /* the frame's padding and border are inside the control height */
    min-height: calc(var(--wa-form-control-height) - 2 * (var(--segmented-pad) + var(--wa-form-control-border-width)));
}

wa-radio[appearance='button']:state(checked) {
    color: var(--wa-color-brand-on-quiet);
}

/* Until the ink is placed, the checked segment carries the fill itself. */
.segmented-control:not([data-glide-ready]) wa-radio[appearance='button']:state(checked) {
    background-color: var(--glide-ink-bg);
}

@media (hover: hover) {
    wa-radio[appearance='button']:hover:not(:state(checked), :state(disabled)) {
        background-color: var(--glass-item-hover);
    }
}

.segmented-icon {
    margin-inline-end: var(--wa-space-2xs);
}
</style>
