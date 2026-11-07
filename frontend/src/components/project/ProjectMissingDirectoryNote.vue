<script setup>
/**
 * ProjectMissingDirectoryNote - the one-line reason under a project's path when
 * its working directory is gone. Renders nothing when the directory is there.
 *
 * The warning icon alone (ProjectMissingDirectoryIcon) only reads on hover, and
 * a tooltip is out of reach on touch — so wherever there is room for a second
 * line, this states it in plain text. Left out where there is not: the compact
 * project header, and the sidebar rows.
 *
 * The project dialog says the same thing in its own markup, because there the
 * sentence carries the "Re-check" button and changes wording after an attempt.
 */
import { computed } from 'vue'
import { useDataStore } from '../../stores/data'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
})

const store = useDataStore()
const missing = computed(() => !!store.getProject(props.projectId)?.stale)
</script>

<template>
    <div v-if="missing" class="missing-directory-note">{{ $t('project.edit.directory.missing') }}</div>
</template>

<style scoped>
.missing-directory-note {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-warning-on-quiet);
}
</style>
