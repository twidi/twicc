<script setup>
/**
 * ProjectMissingDirectoryDialog - why "New session" does not work here.
 *
 * Every entry point that would start a session in a project whose directory is
 * gone stays VISIBLE and keeps its disabled look, but remains clickable (with a
 * `help` cursor) and opens this dialog. Hiding the entry point instead made the
 * user wonder where it went; a plain disabled one gave no reason.
 *
 * The dialog names the project, shows the path, and offers the same "Re-check"
 * as the project edit dialog — the only way to clear the flag without a
 * restart. A successful re-check closes the dialog: the entry point the user
 * came from is live again.
 */
import { computed, ref } from 'vue'
import { useDataStore } from '../../stores/data'
import { useProjectDirectoryRecheck } from '../../composables/useProjectDirectoryRecheck'
import ProjectBadge from './ProjectBadge.vue'
import ProjectDirectoryPath from './ProjectDirectoryPath.vue'

const props = defineProps({
    projectId: {
        type: String,
        default: null,
    },
})

const store = useDataStore()
const dialogRef = ref(null)

const project = computed(() => (props.projectId ? store.getProject(props.projectId) : null))
const { busy: recheckBusy, outcome: recheckOutcome, recheck, reset } = useProjectDirectoryRecheck()

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
}

async function handleRecheck() {
    const updated = await recheck(props.projectId)
    // Back on disk: nothing left to explain, and the entry point the user came
    // from works again.
    if (updated && !updated.stale) {
        close()
    }
}

function open() {
    reset()
    if (dialogRef.value) {
        dialogRef.value.open = true
    }
}

defineExpose({ open })
</script>

<template>
    <wa-dialog ref="dialogRef" :label="$t('project.missingDirectory.title')" class="missing-directory-dialog">
        <div class="missing-directory-body">
            <div class="missing-directory-project">
                <ProjectBadge v-if="projectId" :project-id="projectId" />
            </div>
            <ProjectDirectoryPath v-if="projectId && project?.directory" :project-id="projectId" class="missing-directory-path" />
            <p class="missing-directory-text">
                {{ $t('project.missingDirectory.description') }}
            </p>
            <wa-callout v-if="recheckOutcome === 'confirmed'" variant="warning" size="small">
                {{ $t('project.missingDirectory.stillMissing') }}
            </wa-callout>
            <wa-callout v-else-if="recheckOutcome === 'error'" variant="danger" size="small">
                {{ $t('project.missingDirectory.recheckFailed') }}
            </wa-callout>
        </div>
        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" :disabled="recheckBusy" @click="close">
                {{ $t('common.actions.close') }}
            </wa-button>
            <wa-button variant="brand" :loading="recheckBusy" :disabled="recheckBusy" @click="handleRecheck">
                <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                {{ $t('project.trust.recheck') }}
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.missing-directory-dialog {
    --width: min(480px, calc(100vw - 2rem));
}

.missing-directory-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.missing-directory-project {
    font-weight: var(--wa-font-weight-semibold);
}

.missing-directory-path {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.missing-directory-text {
    margin: 0;
    color: var(--wa-color-text-quiet);
}

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
    align-items: center;
}
</style>
