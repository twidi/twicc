<script setup>
// WorktreePickerRows.vue — renders, inside a "New session" project-picker
// dropdown, a collapsible "Worktrees (N)" entry for one parent project,
// followed (when expanded) by that project's worktree entries.
//
// Mirrors WorktreeSelectorRows, but tuned for the lighter "new session"
// pickers: each worktree row is a bare <ProjectBadge hide-parent> (mark +
// worktree folder), with NO selection check mark and NO state indicators —
// matching how those pickers render normal projects (unlike the navigation selector, whose rows
// carry every state indicator). Purely presentational and emits no events:
// the expand/collapse toggle is driven by the parent dropdown's wa-select
// handler via the special "worktrees-toggle:<id>" value.
//
// Each worktree row's value is its own project id, so selecting it creates a
// new session in that worktree exactly like selecting a normal project.
import { computed } from 'vue'
import ProjectBadge from './ProjectBadge.vue'

const props = defineProps({
    // Main repository project these worktrees belong to.
    parentId: { type: String, required: true },
    // Pickable worktrees (already filtered by the caller's archived/stale rules).
    worktrees: { type: Array, required: true },
    // Whether the "Worktrees" entry is currently expanded.
    expanded: { type: Boolean, default: false },
    // Indent depth of the parent project row; the "Worktrees" header sits one
    // level deeper, the worktree rows one more (mirrors the directory tree).
    baseDepth: { type: Number, default: 0 },
})

const headerDepth = computed(() => props.baseDepth + 1)
const itemDepth = computed(() => props.baseDepth + 2)
</script>

<template>
    <template v-if="worktrees.length">
        <wa-dropdown-item :value="`worktrees-toggle:${parentId}`" class="worktrees-toggle-item">
            <span class="worktree-picker-content" :style="{ paddingLeft: `${headerDepth * 12}px` }">
                <wa-icon name="code-branch" class="worktrees-icon"></wa-icon>
                Worktrees ({{ worktrees.length }})
                <wa-icon :name="expanded ? 'chevron-down' : 'chevron-right'" class="worktrees-toggle-caret"></wa-icon>
            </span>
        </wa-dropdown-item>
        <template v-if="expanded">
            <wa-dropdown-item v-for="wt in worktrees" :key="wt.id" :value="wt.id">
                <span class="worktree-picker-content" :style="{ paddingLeft: `${itemDepth * 12}px` }">
                    <ProjectBadge :project-id="wt.id" hide-parent />
                </span>
            </wa-dropdown-item>
        </template>
    </template>
</template>

<style scoped>
.worktree-picker-content {
    display: flex;
    align-items: center;
    flex: 1;
    min-width: 0;
    gap: var(--wa-space-xs);
}

.worktrees-icon {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    flex-shrink: 0;
}

.worktrees-toggle-caret {
    font-size: var(--wa-font-size-xs);
}
</style>
