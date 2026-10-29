<script setup>
// Links following the "Model & effort picker" heading. The mode switch
// (simple picker / score matrix) sits at the right end of the heading line. In
// matrix mode, the help link and (pointer devices only) the hover-details
// toggle go on their own line below the heading (the heading is a wrapping flex
// row, so a full-width item wraps). Touch devices have no toggle: the details
// panel there is the long-press one. The simple picker has no scores to
// explain, so it has neither link.
import { useSettingsStore } from '../../stores/settings'
import { useBenchmarkTaskStore } from '../../stores/benchmarkTask'
import HelpTextLink from '../help/HelpTextLink.vue'
import HoverDetailsToggle from './HoverDetailsToggle.vue'

const settingsStore = useSettingsStore()
const taskStore = useBenchmarkTaskStore()

function toggleMode() {
    taskStore.setPickerMode(taskStore.pickerMode === 'matrix' ? 'simple' : 'matrix')
}
</script>

<template>
    <button type="button" class="picker-mode-link" @click="toggleMode">
        {{ taskStore.pickerMode === 'matrix' ? 'Show simple picker' : 'Show score matrix' }}
    </button>
    <template v-if="taskStore.pickerMode === 'matrix'">
        <span v-if="settingsStore.isTouchDevice" class="matrix-heading-links-touch">
            <HelpTextLink help-key="model-effort-score" label="What are those numbers?" />
        </span>
        <span v-else class="matrix-heading-links">
            <HelpTextLink help-key="model-effort-score" label="What are those numbers?" />
            <HoverDetailsToggle />
        </span>
    </template>
</template>

<style scoped>
.picker-mode-link {
    margin-left: auto;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    font-size: var(--wa-font-size-xs);
    line-height: 1.2;
    color: var(--wa-color-brand-60);
    &:hover {
        text-decoration: underline;
    }
}

/* The links row is a block of its own in the heading: size its line box to the
   small links, not to the heading's text (which would add an empty strip). */
.matrix-heading-links-touch {
    flex-basis: 100%;
    font-size: var(--wa-font-size-xs);
    line-height: 1.2;
}

.matrix-heading-links {
    flex-basis: 100%;
    font-size: var(--wa-font-size-xs);
    line-height: 1.2;
    display: flex;
    align-items: center;
    column-gap: var(--wa-space-s);
}
</style>
