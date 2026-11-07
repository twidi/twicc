<script setup>
// A small inline "?" affordance that opens a help page on click — without the
// "Don't show this again" switch (manual open). Drop it next to a label or
// field whose feature has a help page: <HelpIconButton help-key="worktrees" />.
// Reuse it for every future help-icon placement instead of re-wiring showHelp.
import { showHelp } from './showHelp'

const props = defineProps({
    helpKey: { type: String, required: true },
    label: { type: String, default: null },
})

function onActivate() {
    showHelp(props.helpKey, { showDontShowAgain: false })
}
</script>

<template>
    <button
        type="button"
        class="help-icon-button"
        :title="label || $t('help.controls.learnMore')"
        :aria-label="label || $t('help.controls.learnMore')"
        @click="onActivate"
    >
        <wa-icon name="circle-question"></wa-icon>
    </button>
</template>

<style scoped>
.help-icon-button {
    display: inline-flex;
    align-items: center;
    padding: 0;
    border: none;
    background: none;
    cursor: pointer;
    color: var(--wa-color-neutral-on-quiet, #888);
    font-size: 0.95em;
    line-height: 1;
    vertical-align: -0.08em;
}

.help-icon-button:hover,
.help-icon-button:focus-visible {
    color: var(--wa-color-brand-60, #6366f1);
    outline: none;
}
</style>
