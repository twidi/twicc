<script setup>
/**
 * ProjectDirectoryPath - a project's working directory, flagged when it is gone.
 *
 * Renders the path as plain text, and turns it into a warning (icon +
 * strikethrough) when the project is stale, so the state reads on the value it
 * is about rather than as a detached banner. See ProjectMissingDirectoryIcon
 * for what "stale" means and why it needs a manual re-check.
 *
 * Inherits font-size and base color from its container, so every list, card and
 * header keeps its own typography.
 *
 * `emphasizeLast` switches to a single-line layout for tight spots: the path is cut from the
 * left when it does not fit (the last folder is what identifies the checkout, so it always
 * stays visible) and the last folder is emphasised.
 */
import { computed } from 'vue'
import { useDataStore } from '../../stores/data'
import ProjectMissingDirectoryIcon from './ProjectMissingDirectoryIcon.vue'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
    emphasizeLast: {
        type: Boolean,
        default: false,
    },
})

const store = useDataStore()
const project = computed(() => store.getProject(props.projectId))
const directory = computed(() => project.value?.directory || '')
const missing = computed(() => !!project.value?.stale)

// Directory split in parent folders + last folder (trailing slashes ignored).
const parts = computed(() => {
    const dir = directory.value.length > 1 ? directory.value.replace(/\/+$/, '') : directory.value
    const cut = dir.lastIndexOf('/') + 1
    return { head: dir.slice(0, cut), last: dir.slice(cut) }
})
</script>

<template>
    <span class="directory-path" :class="{ 'is-missing': missing, 'is-start-truncated': emphasizeLast }">
        <ProjectMissingDirectoryIcon :project-id="projectId" />
        <span v-if="emphasizeLast" class="directory-path-text directory-path-clip"><span class="directory-path-inner">{{ parts.head }}<strong>{{ parts.last }}</strong></span></span>
        <span v-else class="directory-path-text">{{ directory }}</span>
    </span>
</template>

<style scoped>
.directory-path {
    word-break: break-all;
}

.directory-path.is-missing {
    color: var(--wa-color-warning-on-quiet);
}

.directory-path.is-missing .directory-path-text {
    text-decoration: line-through;
    text-decoration-color: color-mix(in oklab, currentColor 50%, transparent);
}

/* Single-line variant. direction: rtl moves the ellipsis to the START of the path; the inner span
   restores ltr so the slashes stay in place. */
.directory-path.is-start-truncated {
    display: flex;
    align-items: center;
    min-width: 0;
    word-break: normal;
}

.directory-path-clip {
    direction: rtl;
    text-align: left;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
}

.directory-path-inner {
    direction: ltr;
    unicode-bidi: embed;
}

.directory-path-inner strong {
    color: var(--wa-color-text-normal);
    font-weight: 650;
}

/* :deep — ProjectMissingDirectoryIcon has two root nodes, so Vue does not stamp
   this component's scope id on its icon. */
.directory-path :deep(.missing-directory-icon) {
    margin-inline-end: var(--wa-space-2xs);
    vertical-align: -0.1em;
}
</style>
