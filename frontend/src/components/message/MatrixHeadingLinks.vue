<script setup>
// Links following the "Model & effort picker" heading of the matrix. Pointer
// devices get the help link and the hover-details toggle together on their own
// line below the heading (the heading is a wrapping flex row, so a full-width
// item wraps). Touch devices keep the help link inline and have no toggle: the
// details panel there is the long-press one.
import { useSettingsStore } from '../../stores/settings'
import HelpTextLink from '../help/HelpTextLink.vue'
import HoverDetailsToggle from './HoverDetailsToggle.vue'

const settingsStore = useSettingsStore()
</script>

<template>
    <span v-if="settingsStore.isTouchDevice" class="matrix-heading-links-touch">
        <HelpTextLink help-key="model-effort-score" label="What are those numbers?" />
    </span>
    <span v-else class="matrix-heading-links">
        <HelpTextLink help-key="model-effort-score" label="What are those numbers?" />
        <HoverDetailsToggle />
    </span>
</template>

<style scoped>
.matrix-heading-links-touch {
    display: contents;
}

.matrix-heading-links {
    flex-basis: 100%;
    display: flex;
    align-items: center;
    column-gap: var(--wa-space-s);
}
</style>
