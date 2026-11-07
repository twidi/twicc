<script setup>
// A small inline text link that opens a help page on click — the text-link
// sibling of HelpIconButton (same showHelp wiring, no "Don't show again"
// switch). Drop it next to a title/label whose feature has a help page:
// <HelpTextLink help-key="model-effort-score" label="What are those numbers?" />.
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
    <a
        class="help-text-link"
        href="#"
        :aria-label="label || $t('help.controls.learnMore')"
        @click.prevent="onActivate"
    >{{ label || $t('help.controls.learnMore') }}</a>
</template>

<style scoped>
.help-text-link {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-normal);
    color: var(--wa-color-brand-60);
    cursor: pointer;
    text-decoration: none;
    white-space: nowrap;
}

.help-text-link:hover,
.help-text-link:focus-visible {
    text-decoration: underline;
    outline: none;
}
</style>
