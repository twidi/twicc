<script setup>
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
/**
 * CodeCommentsIndicator - Shows a comment icon when there are unsent code comments.
 *
 * Two usage modes:
 * - Pass `projectIds` to aggregate counts from the store (project/workspace level).
 * - Pass `count` directly for finer granularities (session, source, file level).
 *
 * Wrapped in a single root <span> so it can be placed in web component slots
 * (e.g. slot="end" on wa-button).
 */
import { computed, useId } from 'vue'
import { useCodeCommentsStore } from '../../stores/codeComments'
import { useDataStore } from '../../stores/data'
import AppTooltip from './AppTooltip.vue'

const props = defineProps({
    /**
     * List of project IDs to count code comments for.
     * Mutually exclusive with `count`.
     */
    projectIds: {
        type: Array,
        default: null,
    },
    /**
     * Direct count of code comments (for finer granularities).
     * Mutually exclusive with `projectIds`.
     */
    count: {
        type: Number,
        default: null,
    },
    /**
     * Whether to show a tooltip with the count.
     * Disable in compact contexts like dropdown options.
     */
    showTooltip: {
        type: Boolean,
        default: true,
    },
})

const codeCommentsStore = useCodeCommentsStore()
const dataStore = useDataStore()

/**
 * Worktree-expanded, deduped project ids: each id passed in is expanded to its
 * badge scope (the project plus its visible worktrees) so a project's comment
 * badge aggregates its worktrees one level down — consistent with the process
 * indicator placed alongside it. The Set dedup is mandatory here (unlike the
 * process indicator): `countByProjects` sums per-project counts, so the
 * idempotent re-expansion of an already-expanded workspace set would otherwise
 * double-count a worktree.
 */
const effectiveProjectIds = computed(() => {
    if (!props.projectIds) return null
    const set = new Set()
    for (const id of props.projectIds) {
        for (const scopeId of dataStore.getProjectIndicatorScopeIds(id)) set.add(scopeId)
    }
    return [...set]
})

const effectiveCount = computed(() => {
    if (props.count !== null) return props.count
    if (effectiveProjectIds.value) return codeCommentsStore.countByProjects(effectiveProjectIds.value)
    return 0
})

const tooltipText = computed(() => {
    const n = effectiveCount.value
    return t('common.codeComments.unsentCount', { count: n }, n)
})

const indicatorId = useId()
</script>

<template>
    <span v-if="effectiveCount > 0" class="code-comments-indicator">
        <wa-icon :id="indicatorId" name="comment" variant="regular"></wa-icon>
        <AppTooltip v-if="showTooltip" :for="indicatorId">{{ tooltipText }}</AppTooltip>
    </span>
</template>

<style scoped>
.code-comments-indicator {
    display: inline-flex;
    align-items: center;
    color: var(--wa-color-brand);
}
</style>
