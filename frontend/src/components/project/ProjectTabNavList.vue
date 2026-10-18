<script setup>
// ProjectTabNavList.vue - The navigation list of a scope page (workspaces, projects, worktrees) at the top of a
// tab's content, then its divider. Compact-height mode only (utils/compactHeight.js): the page header drops its
// own copy of the list then, and this one scrolls away with the tab's content. Used by the Stats and Timeline
// tabs, above their sticky band (StickyBand.vue).

import ProjectDetailNavList from './ProjectDetailNavList.vue'

defineProps({
    /** Project ID, `ws:<id>` of a workspace, or ALL_PROJECTS_ID. */
    projectId: {
        type: String,
        required: true,
    },
})
</script>

<template>
    <div class="project-tab-nav">
        <ProjectDetailNavList :project-id="projectId" class="project-tab-nav-list" />
        <wa-divider class="project-tab-nav-divider"></wa-divider>
    </div>
</template>

<style scoped>
.project-tab-nav {
    display: none;
}

:where(html.compact-height) .project-tab-nav {
    display: block;
}

.project-tab-nav-list {
    padding: var(--wa-space-s) var(--wa-space-m);
}

.project-tab-nav-divider {
    --spacing: 0;
    --width: var(--divider-size);
}
</style>
