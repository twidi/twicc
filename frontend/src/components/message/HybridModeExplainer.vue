<script setup>
// Shared hybrid-mode explainer, rendered as a wa-details. Single source of truth
// so the toggle dialog (MessageInput) and the Claude settings section stay in
// sync. Pass ``open`` to expand it by default.
//
// Each sentence leads with a bold inline mini-title so the reader can scan the
// bold lead-ins to find the line they need; UI references (the Claude CLI
// terminal, the Settings path) stay plain to keep one bold anchor per line.
import { computed } from 'vue'
import { useSettingsStore } from '../../stores/settings'

defineProps({
    open: {
        type: Boolean,
        default: false,
    },
})

// First-time readers get the persuasive "why you need it" summary; once they've
// seen it (the synced flag flips after the dialog is acknowledged), it reads as
// plain reference — "what is hybrid mode".
const settingsStore = useSettingsStore()
const explainerSeen = computed(() => settingsStore.isClaudeHybridExplainerSeen)
</script>

<template>
    <wa-details :open="open" class="hybrid-mode-explainer">
        <i18n-t v-if="!explainerSeen" keypath="session.hybridExplainer.summaryNew" scope="global" tag="span" slot="summary">
            <template #need><strong><em>{{ $t('session.hybridExplainer.need') }}</em></strong></template>
        </i18n-t>
        <span v-else slot="summary">{{ $t('session.hybridExplainer.summary') }}</span>

        <wa-callout variant="neutral" size="small" class="explainer-maintenance-note">
            <wa-icon slot="icon" name="screwdriver-wrench" variant="classic"></wa-icon>
            <i18n-t keypath="session.hybridExplainer.maintenance" scope="global">
                <template #lead><strong>{{ $t('session.hybridExplainer.maintenanceLead') }}</strong></template>
                <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- This environment flag is a technical identifier. -->
                <template #flag><code>TWICC_CLAUDE_HYBRID_ENABLED</code></template>
            </i18n-t>
        </wa-callout>

        <wa-callout variant="warning" size="small">
            <wa-icon slot="icon" name="triangle-exclamation" variant="classic"></wa-icon>
            <i18n-t keypath="session.hybridExplainer.warning" scope="global">
                <template #lead><strong>{{ $t('session.hybridExplainer.warningLead') }}</strong></template>
            </i18n-t>
        </wa-callout>

        <p class="explainer-section-title">{{ $t('session.hybridExplainer.changeTitle') }}</p>
        <i18n-t keypath="session.hybridExplainer.change" scope="global" tag="p">
            <template #lead><strong>{{ $t('session.hybridExplainer.changeLead') }}</strong></template>
            <template #date><a href="https://x.com/ClaudeDevs/status/2054610152817619388" target="_blank" rel="noopener">{{ $t('session.hybridExplainer.changeDate') }}</a></template>
        </i18n-t>
        <i18n-t keypath="session.hybridExplainer.plan" scope="global" tag="p">
            <template #lead><strong>{{ $t('session.hybridExplainer.planLead') }}</strong></template>
        </i18n-t>

        <p class="explainer-section-title">{{ $t('session.hybridExplainer.twiccTitle') }}</p>
        <i18n-t keypath="session.hybridExplainer.terminal" scope="global" tag="p">
            <template #lead><strong>{{ $t('session.hybridExplainer.terminalLead') }}</strong></template>
        </i18n-t>
        <i18n-t keypath="session.hybridExplainer.input" scope="global" tag="p">
            <template #lead><strong>{{ $t('session.hybridExplainer.inputLead') }}</strong></template>
        </i18n-t>
        <i18n-t keypath="session.hybridExplainer.preserved" scope="global" tag="p">
            <template #lead><strong>{{ $t('session.hybridExplainer.preservedLead') }}</strong></template>
        </i18n-t>
        <i18n-t keypath="session.hybridExplainer.streaming" scope="global" tag="p">
            <template #lead><strong>{{ $t('session.hybridExplainer.streamingLead') }}</strong></template>
            <template #thinking><em>{{ $t('session.hybridExplainer.thinking') }}</em></template>
        </i18n-t>
        <i18n-t keypath="session.hybridExplainer.automatic" scope="global" tag="p">
            <template #lead><strong>{{ $t('session.hybridExplainer.automaticLead') }}</strong></template>
        </i18n-t>
        <i18n-t keypath="session.hybridExplainer.orchestration" scope="global" tag="p">
            <template #lead><strong>{{ $t('session.hybridExplainer.orchestrationLead') }}</strong></template>
        </i18n-t>

        <p class="explainer-section-title">{{ $t('session.hybridExplainer.activationTitle') }}</p>
        <i18n-t keypath="session.hybridExplainer.activation" scope="global" tag="p">
            <template #lead><strong>{{ $t('session.hybridExplainer.activationLead') }}</strong></template>
            <template #icon><wa-icon class="inline-terminal-icon" name="terminal" variant="classic"></wa-icon></template>
        </i18n-t>
        <i18n-t keypath="session.hybridExplainer.oneWay" scope="global" tag="p">
            <template #lead><strong>{{ $t('session.hybridExplainer.oneWayLead') }}</strong></template>
        </i18n-t>
    </wa-details>
</template>

<style scoped>
.hybrid-mode-explainer p {
    margin: 0 0 var(--wa-space-s) 0;
    line-height: 1.5;
}

/* Section labels between the groups of sentences. Scoped with two classes so
   they beat ``.hybrid-mode-explainer p``. */
.hybrid-mode-explainer .explainer-section-title {
    margin: var(--wa-space-l) 0 var(--wa-space-2xs);
    font-size: var(--wa-font-size-l);
    font-weight: var(--wa-font-weight-bold);
    color: var(--wa-color-text-quiet);
}

.inline-terminal-icon {
    vertical-align: -0.1em;
    color: var(--wa-color-brand-fill-loud);
}
</style>
