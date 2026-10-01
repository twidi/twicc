<script setup>
// Compact counts of uncommitted (or per-commit) file changes: modified / added / deleted /
// conflicted, each shown only when non-zero. Renders nothing when every count is zero. Shared by
// the Git panel header and the Git tab label (center strip, dock tab bars, gutter chips).
import { computed } from 'vue'
import pencilIcon from './GitLog/assets/pencil.svg'
import plusIcon from './GitLog/assets/plus.svg'
import minusIcon from './GitLog/assets/minus.svg'

const props = defineProps({
    /** File change counts: { modified, added, deleted, conflicted }. */
    stats: {
        type: Object,
        default: null,
    },
})

const hasStats = computed(() => {
    const s = props.stats
    if (!s) return false
    return s.modified > 0 || s.added > 0 || s.deleted > 0 || s.conflicted > 0
})
</script>

<template>
    <span v-if="hasStats" class="status-badges">
        <span v-if="stats.modified > 0" class="status-badge modified">
            <span class="status-count">{{ stats.modified }}</span>
            <img :src="pencilIcon" class="status-icon" alt="modified">
        </span>

        <span v-if="stats.added > 0" class="status-badge added">
            <span class="status-count">{{ stats.added }}</span>
            <img :src="plusIcon" class="status-icon" alt="added">
        </span>

        <span v-if="stats.deleted > 0" class="status-badge deleted">
            <span class="status-count">{{ stats.deleted }}</span>
            <img :src="minusIcon" class="status-icon" alt="deleted">
        </span>

        <span v-if="stats.conflicted > 0" class="status-badge conflicted">
            <span class="status-count">{{ stats.conflicted }}</span>
            <wa-icon name="triangle-exclamation" class="status-icon" label="conflicts"></wa-icon>
        </span>
    </span>
</template>

<style scoped>
.status-badges {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
}

.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.125rem;
}

.status-count {
    font-size: var(--wa-font-size-xs);
    font-variant-numeric: tabular-nums;
}

/* max-width: none undoes WA native.css `img { max-width: 100% }`: a percentage max-width makes an
   image count as zero in its container's intrinsic width, so a tab sized to its content (the
   center strip) left the icons overflowing onto the next tab. */
.status-icon {
    height: 0.7rem;
    width: 0.7rem;
    max-width: none;
    flex-shrink: 0;
}

.status-badge.modified .status-count {
    color: #e5a935;
}

.status-badge.added .status-count {
    color: #5dc044;
}

.status-badge.deleted .status-count {
    color: #FF757C;
}

.status-badge.conflicted .status-count {
    color: #ff5c5c;
}

.status-badge.conflicted .status-icon {
    color: #ff5c5c;
    font-size: 0.7rem;
}
</style>
