<script setup>
// ProjectTrustDialog.vue — asks the user whether to trust a project before the
// first session is started in it. Imperative, promise-based: a parent calls
// `requestDecision(project)` and awaits `{ trusted, propagation }` or `null`
// (cancelled). The caller is responsible for persisting the decision (POST
// /api/projects/<id>/trust/decide/). See ProjectEditDialog.vue for the dialog
// pattern, and docs/plans/2026-06-09-project-trust-design.md §5.
import { ref } from 'vue'
import { defaultTrustPropagation } from '../../utils/trust'
import ProjectBadge from './ProjectBadge.vue'

const dialogRef = ref(null)
const project = ref(null)
const propagation = ref(false)

// The pending promise's resolver; consumed exactly once per open.
let resolveDecision = null

function settle(value) {
    const resolve = resolveDecision
    resolveDecision = null
    if (dialogRef.value) dialogRef.value.open = false
    if (resolve) resolve(value)
}

/**
 * Open the dialog for `proj` and resolve with the user's decision.
 * @returns {Promise<{trusted: boolean, propagation: boolean} | null>}
 *   null when the user cancels / dismisses the dialog.
 */
function requestDecision(proj) {
    // If a previous request is somehow still pending, cancel it first.
    if (resolveDecision) settle(null)
    project.value = proj
    propagation.value = defaultTrustPropagation(proj)
    return new Promise((resolve) => {
        resolveDecision = resolve
        if (dialogRef.value) dialogRef.value.open = true
    })
}

function decide(trusted) {
    settle({ trusted, propagation: propagation.value })
}

// User dismissed via Escape / backdrop / the X — treat as cancel. Guard against
// the event bubbling from a nested wa-switch and against our own programmatic
// close (resolveDecision is already null by then).
function handleHide(event) {
    if (event.target !== dialogRef.value) return
    if (resolveDecision) settle(null)
}

defineExpose({ requestDecision })
</script>

<template>
    <wa-dialog ref="dialogRef" label="Trust this project?" class="project-trust-dialog" @wa-hide="handleHide">
        <div class="dialog-content">
            <p class="intro">
                Is this a project you created or trust (your own code, a well-known
                open-source project, or your team's work)? Claude Code and Codex will be
                able to read, edit and run files here.
            </p>

            <div v-if="project" class="project-info">
                <ProjectBadge :project-id="project.id" class="project-name" />
                <div class="project-dir">{{ project.directory }}</div>
            </div>

            <wa-switch
                class="propagate-switch"
                :checked="propagation"
                @change="propagation = $event.target.checked"
                size="small"
            >
                Apply to sub-folders and worktrees
                <span class="propagate-hint">
                    — descendants inherit this decision until you decide otherwise.
                </span>
            </wa-switch>
        </div>

        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="settle(null)">
                Cancel
            </wa-button>
            <wa-button variant="neutral" @click="decide(false)">
                <wa-icon slot="start" name="xmark"></wa-icon>
                Don't trust
            </wa-button>
            <wa-button variant="brand" @click="decide(true)">
                <wa-icon slot="start" name="shield"></wa-icon>
                Trust
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.project-trust-dialog {
    --width: min(520px, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.intro {
    margin: 0;
    font-size: var(--wa-font-size-m);
    color: var(--wa-color-text-base);
}

.project-info {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-xs) var(--wa-space-s);
}

.project-name {
    font-weight: var(--wa-font-weight-semibold);
}

.project-dir {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    word-break: break-all;
}

.propagate-switch {
    font-size: var(--wa-font-size-s);
}

.propagate-hint {
    color: var(--wa-color-text-quiet);
}

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
}
</style>
