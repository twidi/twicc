<script setup>
// WorktreeSelectorRows.vue — renders, inside the sidebar project selector's
// flat dropdown list, a collapsible "Worktrees (N)" entry for one parent
// project, followed (when expanded) by that project's worktree entries.
//
// These are plain wa-dropdown-item rows (NOT a wa submenu / flyout), simply
// indented under their parent project. The expand/collapse toggle is driven by
// the parent dropdown's wa-select handler via the special
// "worktrees-toggle:<id>" value — this component is purely presentational and
// emits no events.
//
// Each worktree row is rendered with the SAME <ProjectSelectorRow> as a normal
// project (so it inherits every state indicator identically); the only
// differences are the deeper indentation and `hide-parent` (the main repository
// is the row right above).
import { computed } from 'vue'
import ProjectSelectorRow from './ProjectSelectorRow.vue'
import AggregatedProcessIndicator from '../ui/AggregatedProcessIndicator.vue'

const props = defineProps({
    // Main repository project these worktrees belong to.
    parentId: { type: String, required: true },
    // Visible worktrees (already filtered by the caller's archived setting).
    worktrees: { type: Array, required: true },
    // Whether the "Worktrees" entry is currently expanded.
    expanded: { type: Boolean, default: false },
    // Indent depth of the parent project row; the "Worktrees" header sits one
    // level deeper, the worktree rows one more (mirrors the directory tree).
    baseDepth: { type: Number, default: 0 },
    // Currently selected project id (forwarded to the worktree rows).
    currentProjectId: { type: String, default: null },
    isAllProjectsMode: { type: Boolean, default: false },
})

const headerDepth = computed(() => props.baseDepth + 1)
const itemDepth = computed(() => props.baseDepth + 2)

// All worktree ids — the "Worktrees" header shows their aggregated state
// (process / unread / pending …), the same way a workspace row aggregates its
// projects.
const worktreeIds = computed(() => props.worktrees.map(w => w.id))
</script>

<template>
    <template v-if="worktrees.length">
        <wa-dropdown-item :value="`worktrees-toggle:${parentId}`" class="worktrees-toggle-item">
            <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
            <span class="selector-item-content" :style="{ paddingLeft: `${headerDepth * 12}px` }">
                <wa-icon name="code-branch" class="worktrees-icon"></wa-icon>
                Worktrees ({{ worktrees.length }})
                <wa-icon :name="expanded ? 'chevron-down' : 'chevron-right'" class="worktrees-toggle-caret"></wa-icon>
                <span class="selector-item-indicators">
                    <AggregatedProcessIndicator :project-ids="worktreeIds" size="small" />
                </span>
                <!-- This row carries no "…" menu, but reserves the same width so
                     its indicators stay aligned with the project/workspace rows. -->
                <span class="row-menu-placeholder" aria-hidden="true"></span>
            </span>
        </wa-dropdown-item>
        <template v-if="expanded">
            <ProjectSelectorRow
                v-for="wt in worktrees"
                :key="wt.id"
                :project-id="wt.id"
                :depth="itemDepth"
                hide-parent
                :current-project-id="currentProjectId"
                :is-all-projects-mode="isAllProjectsMode"
            />
        </template>
    </template>
</template>

<style scoped>
.selector-item-content {
    display: flex;
    align-items: center;
    flex: 1;
    min-width: 0;
    gap: var(--wa-space-xs);
}

.selector-item-indicators {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    margin-left: auto;
    flex-shrink: 0;
}

.worktrees-icon {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    flex-shrink: 0;
}

.worktrees-toggle-caret {
    font-size: var(--wa-font-size-xs);
}

/* Reserves the exact width of a row's "…" actions button so the "Worktrees (N)"
   header — which has no menu — keeps its indicators aligned with the other rows. */
.row-menu-placeholder {
    flex-shrink: 0;
    width: var(--selector-row-action-size, 1.5rem);
}
</style>
