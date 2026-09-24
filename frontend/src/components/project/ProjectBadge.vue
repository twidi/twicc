<script setup>
// ProjectBadge.vue - Displays a project mark (color dot or icon) and its name.
//
// A git worktree (a project whose `worktree_of` points at its main repository)
// is detected here, so every caller gets the same rendering without a branch
// of its own:
//
//   [mark] <main repo name> <code-branch icon> <worktree folder>
//
// The mark uses the worktree's own color, falling back to its main
// repository's (see useProjectMark). The folder is the worktree's own name if it
// has one, else just the final folder name of its directory (see worktreeLabel).
// When the parent project can't be resolved, or `hideParent` is set, only the
// folder is shown.
import { computed, toRef } from 'vue'
import { RouterLink } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { worktreeLabel } from '../../utils/worktree'
import { projectPathTitle } from '../../utils/projectName'
import { useProjectMark } from '../../composables/useProjectMark'
import ProjectMark from './ProjectMark.vue'
import ProjectMissingDirectoryIcon from './ProjectMissingDirectoryIcon.vue'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
    // Ignored for a worktree, which always shows its own name or final folder.
    useDirectoryForUnnamed: {
        type: Boolean,
        default: false,
    },
    // Explicit label override. When set, it replaces the computed name of the
    // project itself (for a worktree: the folder shown after the main
    // repository). Used for live previews reflecting an unsaved name.
    label: {
        type: String,
        default: null,
    },
    // Explicit dot color override. When non-null (including an empty string,
    // meaning "no color"), it replaces the project's stored color. Used for live
    // previews where the badge must reflect an unsaved color choice. `null` =
    // not overriding (fall back to the project's stored color).
    colorOverride: {
        type: String,
        default: null,
    },
    gap: {
        type: String,
        default: null,
    },
    // Whether to render the leading mark (color dot or icon). Turned off when
    // the caller already draws its own (e.g. the selector trigger).
    dot: {
        type: Boolean,
        default: true,
    },
    // For a worktree: do not show the main repository name, only the worktree
    // folder. Used where worktrees are listed under their main repository,
    // which already says whose worktrees they are.
    hideParent: {
        type: Boolean,
        default: false,
    },
    // For a worktree: the main repository name links to that project's home.
    parentLink: {
        type: Boolean,
        default: false,
    },
    // Opt-in: mark the badge when the project's working directory is gone.
    // Deliberately NOT the default — the badge appears in dozens of places and
    // the mark would become noise. Only the sidebar project selector turns it
    // on: it is the one list where the state is otherwise invisible and where
    // the row is the way to reach the project home, which explains it in full.
    flagMissingDirectory: {
        type: Boolean,
        default: false,
    },
})

const store = useDataStore()

const project = computed(() => store.getProject(props.projectId))
const isWorktree = computed(() => !!project.value?.worktree_of)
const parent = computed(() => {
    const pid = project.value?.worktree_of
    return pid ? store.getProject(pid) : null
})
const showParent = computed(() => !props.hideParent && !!parent.value)
const parentName = computed(() => parent.value ? store.getProjectDisplayName(parent.value.id) : '')
const parentRoute = computed(() => parent.value ? { name: 'project', params: { projectId: parent.value.id } } : null)
// Full directory path of an unnamed main repository, shown on hover.
const parentTitle = computed(() => projectPathTitle(parent.value))

const displayName = computed(() => {
    if (props.label != null) {
        return props.label
    }
    if (isWorktree.value) {
        return worktreeLabel(project.value) || store.getProjectDisplayName(props.projectId)
    }
    if (props.useDirectoryForUnnamed && project.value && !project.value.name) {
        return project.value.directory || store.getProjectDisplayName(props.projectId)
    }
    return store.getProjectDisplayName(props.projectId)
})
// For unnamed projects (shown with just their final folder name), reveal the
// full directory path on hover. Suppressed when the displayed name already is
// the full path (e.g. `useDirectoryForUnnamed`).
const nameTitle = computed(() => {
    const path = projectPathTitle(project.value)
    return path && path !== displayName.value ? path : null
})
const { iconUrl, dotColor } = useProjectMark(toRef(props, 'projectId'), {
    colorOverride: toRef(props, 'colorOverride'),
})
// Whether to flag this project as untrusted (effective trust ≠ trusted, i.e.
// explicitly untrusted OR unknown). Resolved from the store's cached set; a
// worktree already inherits its main repository's trust there.
const untrusted = computed(() => store.untrustedProjectIds.has(props.projectId))
</script>

<template>
    <span class="project-badge" :style="gap ? { '--badge-gap': gap } : null">
        <ProjectMark v-if="dot" :icon-url="iconUrl" :color="dotColor" />
        <template v-if="showParent">
            <RouterLink
                v-if="parentLink && parentRoute"
                :to="parentRoute"
                class="project-badge-name project-badge-parent-link"
                :title="parentTitle"
                @click.stop
            >{{ parentName }}</RouterLink>
            <span v-else class="project-badge-name" :title="parentTitle">{{ parentName }}</span>
            <wa-icon name="code-branch" auto-width class="project-badge-sep"></wa-icon>
        </template>
        <span class="project-badge-name" :title="nameTitle">{{ displayName }}</span>
        <ProjectMissingDirectoryIcon v-if="flagMissingDirectory" :project-id="projectId" />
        <wa-icon
            v-if="untrusted"
            name="lock"
            label="Untrusted project"
            title="This project is not trusted"
            class="project-badge-trust"
        ></wa-icon>
    </span>
</template>

<style scoped>
.project-badge {
    display: inline-flex;
    align-items: center;
    gap: var(--badge-gap, var(--wa-space-xs));
    min-width: 0;
}

/* Callers cap each name (not the whole badge) with
   `--project-badge-name-max-width`, so a worktree's two names both get room. */
.project-badge-name {
    max-width: var(--project-badge-name-max-width, none);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.project-badge-sep {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    font-size: 0.85em;
}

.project-badge-parent-link {
    color: inherit;
    text-decoration: none;
    cursor: pointer;
}

.project-badge-parent-link:hover {
    color: var(--wa-color-brand-text);
    text-decoration: underline;
}

/* Untrusted marker: normal text colour at low opacity — faint, and adapts to
   light/dark and every theme on its own (quieter than --wa-color-text-quiet). */
.project-badge-trust {
    flex-shrink: 0;
    color: var(--wa-color-text-normal);
    opacity: 0.2;
    font-size: 0.85em;
}

/* :deep — ProjectMissingDirectoryIcon has two root nodes (icon + tooltip), so
   Vue does not stamp this component's scope id on its icon. */
.project-badge :deep(.missing-directory-icon) {
    flex-shrink: 0;
    font-size: 0.85em;
}
</style>
