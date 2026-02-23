<script setup>
import { computed, useId } from 'vue'
import AppTooltip from './AppTooltip.vue'
import pencilIcon from './GitLog/assets/pencil.svg'
import plusIcon from './GitLog/assets/plus.svg'
import minusIcon from './GitLog/assets/minus.svg'

const props = defineProps({
    /** The currently selected commit object (or null/undefined). */
    selectedCommit: {
        type: Object,
        default: null,
    },
    /** File change counts: { modified, added, deleted }. */
    stats: {
        type: Object,
        default: null,
    },
    /** Whether stats are currently being loaded. */
    statsLoading: {
        type: Boolean,
        default: false,
    },
    /** Whether the git log overlay is currently open. */
    gitLogOpen: {
        type: Boolean,
        default: false,
    },
    /** Currently selected branch name (shown as a tag). */
    selectedBranch: {
        type: String,
        default: '',
    },
})

const emit = defineEmits(['toggle-git-log'])

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

/** Display text for the commit selector. */
const commitLabel = computed(() => {
    if (!props.selectedCommit) {
        return 'Uncommitted changes'
    }
    // For the index pseudo-commit
    if (props.selectedCommit.hash === 'index') {
        return 'Uncommitted changes'
    }
    return props.selectedCommit.message || props.selectedCommit.hash
})

/** Short hash for display (first 7 chars). */
const commitShortHash = computed(() => {
    if (!props.selectedCommit || props.selectedCommit.hash === 'index') {
        return null
    }
    return props.selectedCommit.hash?.substring(0, 7)
})

const hasStats = computed(() => {
    if (!props.stats) return false
    const s = props.stats
    return s.modified > 0 || s.added > 0 || s.deleted > 0
})

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

const commitLabelId = useId()

function toggleGitLog() {
    emit('toggle-git-log')
}
</script>

<template>
    <div class="git-panel-header">
        <button
            class="commit-selector"
            :class="{ open: gitLogOpen }"
            @click="toggleGitLog"
        >
            <span class="commit-message" :id="commitLabelId">{{ commitLabel }}</span>
            <AppTooltip :for="commitLabelId">{{ commitLabel }}</AppTooltip>

            <div v-if="statsLoading" class="status-badges">
                <wa-spinner style="--spinner-size: 0.75rem;"></wa-spinner>
            </div>

            <div v-else-if="hasStats" class="status-badges">
                <span
                    v-if="stats.modified > 0"
                    class="status-badge modified"
                >
                    <span class="status-count">{{ stats.modified }}</span>
                    <img :src="pencilIcon" class="status-icon" alt="modified">
                </span>

                <span
                    v-if="stats.added > 0"
                    class="status-badge added"
                >
                    <span class="status-count">{{ stats.added }}</span>
                    <img :src="plusIcon" class="status-icon" alt="added">
                </span>

                <span
                    v-if="stats.deleted > 0"
                    class="status-badge deleted"
                >
                    <span class="status-count">{{ stats.deleted }}</span>
                    <img :src="minusIcon" class="status-icon" alt="deleted">
                </span>
            </div>

            <wa-tag v-if="selectedBranch" variant="neutral" class="branch-tag">
                {{ selectedBranch }}
            </wa-tag>

            <wa-tag v-if="commitShortHash" variant="neutral" class="commit-hash">
                {{ commitShortHash }}
            </wa-tag>

            <wa-icon
                class="chevron"
                :name="gitLogOpen ? 'chevron-up' : 'chevron-down'"
            ></wa-icon>
        </button>
    </div>
</template>

<style scoped>
.git-panel-header {
    flex-shrink: 0;
}

/* ----- Commit selector button ----- */

.commit-selector {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    width: 100%;
    padding: var(--wa-space-xs) var(--wa-space-s);
    background: none;
    border: none;
    cursor: pointer;
    font-family: inherit;
    font-size: var(--wa-font-size-s);
    font-weight: normal;
    color: inherit;
    text-align: left;
    transition: background-color 0.15s ease;
    box-shadow: none;
    margin: 0;
    translate: none !important;
    transform: none !important;
    justify-content: start;
    flex-wrap: wrap;
    height: auto;
}

.commit-selector:hover {
    background-color: var(--wa-color-surface-alt);
}

.commit-selector.open {
    background-color: var(--wa-color-surface-alt);
}

/* ----- Branch tag ----- */

.branch-tag {
    font-weight: 600;
    line-height: 1;
    height: auto;
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    margin-left: auto;
}

.commit-hash {
    flex-shrink: 0;
    font-weight: 600;
    line-height: 1;
    height: auto;
    padding: var(--wa-space-2xs) var(--wa-space-xs);
}

.commit-message {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
}

/* ----- Status badges (M / A / D counts) ----- */

.status-badges {
    display: flex;
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

.status-icon {
    height: 0.7rem;
    width: 0.7rem;
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

/* ----- Chevron ----- */

.chevron {
    flex-shrink: 0;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    transition: transform 0.2s ease;
}
</style>
