<script setup>
/**
 * ProjectMissingDirectoryIcon - warning mark for a project whose working
 * directory is gone. Renders nothing when the directory is there.
 *
 * `Project.stale` is a STORED observation ("the directory was gone the last
 * time TwiCC looked"): it is refreshed at startup and by the provider-folder
 * watcher, never re-checked while rendering. Nothing watches the working
 * directories themselves, so a folder restored while TwiCC runs stays flagged
 * until the project dialog's "Re-check" button (or a restart) clears it — hence
 * the wording of the tooltip.
 *
 * Three call sites: inside ProjectDirectoryPath next to the path itself, inside
 * ProjectBadge behind its opt-in `flag-missing-directory` (the sidebar project
 * selector), and standalone next to the name in the compact closed project header.
 *
 * Two root nodes (icon + tooltip), so callers must NOT pass a class or style:
 * there is no single root for Vue to fall them through to. Wrap it instead.
 */
import { computed, useId } from 'vue'
import { useDataStore } from '../../stores/data'
import AppTooltip from '../ui/AppTooltip.vue'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
})

const store = useDataStore()
const missing = computed(() => !!store.getProject(props.projectId)?.stale)
const iconId = `project-missing-dir-${useId()}`
</script>

<template>
    <wa-icon
        v-if="missing"
        :id="iconId"
        name="triangle-exclamation"
        class="missing-directory-icon"
    ></wa-icon>
    <AppTooltip v-if="missing" :for="iconId" hoist>
        Directory not found on disk. TwiCC recorded this at its last check; a restored folder is not detected live.
    </AppTooltip>
</template>

<style scoped>
/* wa-tooltip is `position: absolute` and never takes part in the layout, so the
   second root node is safe wherever the icon itself fits. */
.missing-directory-icon {
    color: var(--wa-color-warning-on-quiet);
}
</style>
