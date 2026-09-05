<script setup>
import { showHelp } from './showHelp'

const props = defineProps({
    helpKey: { type: String, required: true },
    label: { type: String, required: true },
})

function openHelp() {
    showHelp(props.helpKey, { showDontShowAgain: false })
}
</script>

<template>
    <a class="help-feature-link" href="#" @click.prevent="openHelp">
        <wa-icon name="circle-question"></wa-icon>
        <span>{{ label }}</span>
    </a>
</template>

<style scoped>
.help-feature-link {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    color: var(--wa-color-brand-60);
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-normal);
    line-height: var(--wa-line-height-normal);
    text-decoration: none;
}

.help-feature-link:hover,
.help-feature-link:focus-visible {
    text-decoration: underline;
    outline: none;
}
</style>
