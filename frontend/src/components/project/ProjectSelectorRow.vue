<script setup>
// ProjectSelectorRow.vue — one project entry in the sidebar project selector
// dropdown. Single source of truth for a project line: the selection check, the
// project badge (color dot + name) and the end-of-line state indicators
// (code comments + aggregated process/unread). Used for both normal projects
// and worktree entries — a worktree is simply the same row at a deeper `depth`,
// with `hideParent` (it is listed under its main repository). Keeping a single
// row component guarantees worktrees stay in lockstep with normal projects for
// every state indicator.
import { computed, inject } from 'vue'
import { useDataStore } from '../../stores/data'
import ProjectBadge from './ProjectBadge.vue'
import CodeCommentsIndicator from '../ui/CodeCommentsIndicator.vue'
import AggregatedProcessIndicator from '../ui/AggregatedProcessIndicator.vue'

const props = defineProps({
    projectId: { type: String, required: true },
    // Indent level (0 = flush). paddingLeft = depth * 12px, matching the tree.
    depth: { type: Number, default: 0 },
    // Currently selected project id (drives the leading check mark).
    currentProjectId: { type: String, default: null },
    isAllProjectsMode: { type: Boolean, default: false },
    // Forwarded to ProjectBadge: a worktree listed under its main repository
    // shows only its own folder.
    hideParent: { type: Boolean, default: false },
})

const store = useDataStore()

// Provided by ProjectView so any row (named, tree or worktree) can open the
// shared project edit dialog / start a new session without forwarding events
// through every level.
const openProjectEditDialog = inject('openProjectEditDialog', null)
const createSessionInProject = inject('createSessionInProject', null)
const openProjectMissingDirectoryDialog = inject('openProjectMissingDirectoryDialog', null)

const project = computed(() => store.getProject(props.projectId))
const isArchived = computed(() => !!project.value?.archived)
// Stale projects/worktrees have a gone directory — no session can be created
// there, so the "New session" entry stays disabled-looking but live, and
// explains itself through the missing-directory dialog.
const isStale = computed(() => !!project.value?.stale)

// Row "…" menu actions. In the template the child dropdown's wa-select is
// `.stop`ped, so it never bubbles to the selector's own selection handler (which
// would navigate); the selector stays open thanks to ProjectView's wa-hide guard.
function onRowMenuSelect(event) {
    const value = event.detail?.item?.value
    if (value === 'new-session') {
        // The entry stays selectable on a stale project so the click can say why
        // no session starts there (see `.fake-disabled`).
        if (isStale.value) {
            openProjectMissingDirectoryDialog?.(props.projectId)
        } else {
            createSessionInProject?.(props.projectId)
        }
    } else if (value === 'edit') {
        openProjectEditDialog?.(props.projectId)
    } else if (value === 'archive') {
        store.setProjectArchived(props.projectId, true)
    } else if (value === 'unarchive') {
        store.setProjectArchived(props.projectId, false)
    }
}
</script>

<template>
    <wa-dropdown-item :value="projectId" :class="{ 'selector-row-archived': isArchived }">
        <wa-icon slot="icon" name="check" :style="{ visibility: !isAllProjectsMode && currentProjectId === projectId ? 'visible' : 'hidden' }"></wa-icon>
        <span class="selector-item-content" :style="depth ? { paddingLeft: `${depth * 12}px` } : null">
            <!-- flag-missing-directory: the selector is the only list where a
                 gone directory is otherwise invisible. The mark is enough here —
                 selecting the row leads to the project home, which spells it
                 out. -->
            <ProjectBadge :project-id="projectId" :hide-parent="hideParent" flag-missing-directory />
            <span class="selector-item-indicators">
                <CodeCommentsIndicator :project-ids="[projectId]" />
                <AggregatedProcessIndicator :project-ids="[projectId]" size="small" />
            </span>
            <!-- Per-row actions menu. The wrapper's @click.stop prevents the
                 trigger/menu clicks from bubbling to the selector's own item
                 click handler (which would navigate to the project). The child
                 dropdown's own trigger toggle still fires (it is wired one level
                 deeper, inside the dropdown's shadow slot). -->
            <span class="row-menu" @click.stop>
                <wa-dropdown placement="bottom-end" @wa-select.stop="onRowMenuSelect">
                    <wa-button slot="trigger" appearance="plain" size="small" class="row-menu-trigger">
                        <wa-icon name="ellipsis-v" label="Project actions"></wa-icon>
                    </wa-button>
                    <!-- Kept visible on a stale project, in a disabled look but
                         still live (see `.fake-disabled`): removing it made users
                         hunt for an entry that had simply vanished. Selecting it
                         explains the reason instead. -->
                    <wa-dropdown-item value="new-session" :class="{ 'fake-disabled': isStale }">
                        <wa-icon slot="icon" name="plus"></wa-icon>
                        New session
                    </wa-dropdown-item>
                    <wa-divider></wa-divider>
                    <wa-dropdown-item value="edit">
                        <wa-icon slot="icon" name="pencil"></wa-icon>
                        Edit
                    </wa-dropdown-item>
                    <wa-dropdown-item v-if="!isArchived" value="archive">
                        <wa-icon slot="icon" name="box-archive"></wa-icon>
                        Archive
                    </wa-dropdown-item>
                    <wa-dropdown-item v-else value="unarchive">
                        <wa-icon slot="icon" name="box-open"></wa-icon>
                        Unarchive
                    </wa-dropdown-item>
                </wa-dropdown>
            </span>
        </span>
    </wa-dropdown-item>
</template>

<style scoped>
/* Archived project / worktree rows: dimmed so they read as de-emphasized vs
   active ones when "show archived projects" surfaces them in the selector. */
.selector-row-archived {
    opacity: 0.55;
}

/* "Fake disabled": the entry LOOKS disabled but stays live, so selecting it can
   explain why no session starts here. The real `disabled` cannot be used — the
   dropdown filters disabled items out before emitting wa-select, so the action
   would never reach us. We reproduce its two visible effects instead: the dimmed
   opacity, and no hover highlight (a :host(:hover) rule, which outer-document
   styles override). The `help` cursor is the hint that clicking says why. */
.fake-disabled {
    opacity: 0.5;
    cursor: help;
}

.fake-disabled:hover {
    background-color: transparent;
}

.selector-item-content {
    display: flex;
    align-items: center;
    flex: 1;
    min-width: 0;
    gap: var(--wa-space-xs);
    /* Reserve room for the absolutely-positioned "…" overlay (button width +
       gap) so the indicators stay put and aligned with the worktree-header
       spacer. */
    padding-inline-end: calc(var(--selector-row-action-size, 1.5rem) + var(--wa-space-xs));
}

.selector-item-indicators {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    margin-left: auto;
    flex-shrink: 0;
}

/* Per-row "…" actions menu, rendered as a near-full-height overlay pinned to the
   right of the row. The wa-dropdown-item host is position:relative and adds
   vertical padding; without this overlay that padding band would be "dead" space
   where a click selects the project instead of opening the menu — making the
   button easy to miss. inset-block leaves a 1px gap top/bottom so the button
   stays visually separated from the row edges; inset-inline-end matches each
   row's right padding so the button stays flush with the content edge, aligned
   with the worktree-header spacer (which reserves the same width but carries no
   menu). */
.row-menu {
    position: absolute;
    inset-block: 1px;
    inset-inline-end: var(--selector-row-padding-inline-end, 0.5em);
    display: flex;
    align-items: stretch;
    justify-content: center;
    flex-shrink: 0;
    width: var(--selector-row-action-size, 1.5rem);
}

.row-menu-trigger {
    opacity: 0.55;
    font-size: var(--wa-font-size-s);
    transition: opacity 0.12s ease;
}

.row-menu-trigger::part(base) {
    padding: 0;
    min-height: 0;
    width: var(--selector-row-action-size, 1.5rem);
    /* Fill the full-height .row-menu overlay so the entire band opens the menu. */
    height: 100%;
}

wa-dropdown-item:hover .row-menu-trigger,
.row-menu:focus-within .row-menu-trigger {
    opacity: 1;
}
</style>
