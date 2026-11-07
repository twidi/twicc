<script setup>
defineProps({
    pattern: { type: String, default: null },
    fileType: { type: String, default: null },
    path: { type: String, default: null },
    pathIconSrc: { type: String, default: null },
})
</script>

<template>
    <span class="items-details-summary-description grep-summary">
        <i18n-t
            v-if="fileType || path"
            :keypath="fileType ? (path ? 'session.tools.grepSummary.typeAndPath' : 'session.tools.grepSummary.type') : 'session.tools.grepSummary.path'"
            scope="global"
        >
            <template #pattern><code v-if="pattern">{{ pattern }}</code></template>
            <template #type><code>{{ fileType }}</code></template>
            <template #path>
                <span v-if="pathIconSrc" class="items-details-summary-file">
                    <img :src="pathIconSrc" class="items-details-summary-file-icon" loading="lazy" width="16" height="16" />
                    <span>{{ path }}</span>
                </span>
                <span v-else>{{ path }}</span>
            </template>
        </i18n-t>
        <code v-else-if="pattern">{{ pattern }}</code>
    </span>
</template>

<style scoped>
.grep-summary {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
    & > span {
        display: inline-flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }
    code {
        font-size: 1em;
        background: var(--wa-color-neutral-fill-quiet);
        border-radius: var(--wa-border-radius-s);
    }
}
.grep-connector {
    white-space: nowrap;
    flex-shrink: 0;
}
</style>
