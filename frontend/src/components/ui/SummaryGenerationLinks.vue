<script setup>
// The generation links of a summary, with its progress. Bare inline flow items:
// the parent row lays them out next to its own controls.
defineProps({
    running: { type: Boolean, default: false },
    loading: { type: Boolean, default: false },
    canRegenerate: { type: Boolean, default: false },
})
defineEmits(['cancel', 'regenerate'])
</script>

<template>
    <a v-if="running" href="#" class="summary-link" @click.prevent="$emit('cancel')">{{ $t('common.actions.cancel') }}</a>
    <a v-else-if="canRegenerate" href="#" class="summary-link" @click.prevent="$emit('regenerate')">{{ $t('summary.actions.regenerate') }}</a>
    <span v-if="running || loading" class="summary-status" role="status">
        <wa-spinner></wa-spinner> {{ running ? $t('summary.status.generating') : $t('summary.status.loading') }}
    </span>
</template>

<style scoped>
.summary-link { color: var(--wa-color-text-link); text-decoration: var(--wa-link-decoration-default); text-decoration-thickness: 0.09375em; text-underline-offset: 0.125em; }
.summary-link:hover { color: color-mix(in oklab, var(--wa-color-text-link), var(--wa-color-mix-hover)); }
.summary-link, .summary-status { white-space: nowrap; }
.summary-status { display: inline-flex; align-items: center; gap: var(--wa-space-xs); color: var(--wa-color-text-quiet); }
</style>
