<script setup>
// WorktreeSelectOptions.vue — renders, inside a <wa-select> project list, the
// worktrees of one parent project: a "Worktrees (N)" header followed by one
// option per worktree, indented under their main repository.
//
// The wa-option counterpart of WorktreePickerRows (the wa-dropdown-item
// version used by the sidebar's "New session" pickers): same grammar — same
// header, same indentation, a bare <ProjectBadge> per worktree with the main
// repo's color as fallback. A select has no room for the collapsible toggle
// those dropdowns carry, so the worktrees are always listed.
//
// Each option's value is the worktree's own project id, so picking one targets
// that worktree exactly like picking a normal project.
import { computed } from 'vue'
import { useDataStore } from '../../stores/data'
import { worktreeLabel } from '../../utils/worktree'
import ProjectBadge from './ProjectBadge.vue'

const props = defineProps({
    // Main repository project whose worktrees are listed.
    parentId: { type: String, required: true },
    // Indent depth of the parent project option; the header sits one level
    // deeper, the worktree options one more (mirrors the directory tree).
    baseDepth: { type: Number, default: 0 },
    // When set, only the worktrees whose id is in this Set are listed (a
    // select restricted to projects that own something, e.g. peer messages).
    // Null lists every worktree.
    onlyProjectIds: { type: Set, default: null },
})

const dataStore = useDataStore()

// Same rule as the parent projects of every select: archived and stale ones
// are not offered (a stale worktree's directory is gone).
const worktrees = computed(() =>
    dataStore.getWorktreesOf(props.parentId).filter(p =>
        !p.archived && !p.stale && (!props.onlyProjectIds || props.onlyProjectIds.has(p.id))
    )
)

const headerDepth = computed(() => props.baseDepth + 1)
const itemDepth = computed(() => props.baseDepth + 2)

const parentProject = computed(() => dataStore.getProject(props.parentId))
// Worktrees inherit their main repository's color when they have none of their own.
const parentColor = computed(() => parentProject.value?.color || null)

/** Worktree label: its name if any, else just the final folder name. */
function labelFor(wt) {
    return worktreeLabel(wt) || dataStore.getProjectDisplayName(wt.id)
}

/** Label of the select's own button once picked: the leaf name alone would not
 *  say which repository it belongs to. */
function buttonLabelFor(wt) {
    return `${dataStore.getProjectDisplayName(props.parentId)} / ${labelFor(wt)}`
}
</script>

<template>
    <template v-if="worktrees.length">
        <wa-option disabled class="worktrees-header-option">
            <span class="worktree-option" :style="{ paddingLeft: `${headerDepth * 12}px` }">
                <wa-icon name="code-branch" auto-width class="worktrees-icon"></wa-icon>
                Worktrees ({{ worktrees.length }})
            </span>
        </wa-option>
        <wa-option v-for="wt in worktrees" :key="wt.id" :value="wt.id" :label="buttonLabelFor(wt)">
            <span class="worktree-option" :style="{ paddingLeft: `${itemDepth * 12}px` }">
                <ProjectBadge :project-id="wt.id" :label="labelFor(wt)" :fallback-color="parentColor" />
            </span>
        </wa-option>
    </template>
</template>

<style scoped>
.worktree-option {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.worktrees-header-option {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.worktrees-icon {
    font-size: var(--wa-font-size-s);
    flex-shrink: 0;
}
</style>
