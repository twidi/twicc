<script setup>
import { computed } from 'vue'
import { useDataStore } from '../stores/data'

const store = useDataStore()

const syncProgress = computed(() => store.initialSyncProgress)
const computeProgress = computed(() => store.backgroundComputeProgress)

// Show callout if any phase is active (not null and not completed)
const isVisible = computed(() => {
    return (syncProgress.value && !syncProgress.value.completed) ||
           (computeProgress.value && !computeProgress.value.completed)
})

// Sync is done: either explicitly completed, or absent (cleared by backend after completion).
// The latter happens when the page is loaded mid-compute — sync state is already gone.
const isSyncDone = computed(() =>
    syncProgress.value?.completed === true || !syncProgress.value
)

// Sync is actively running (not yet completed)
const isSyncActive = computed(() =>
    syncProgress.value && !syncProgress.value.completed
)

// Show compute counter and hint only once sync is done and compute is actively running
const isComputeActive = computed(() =>
    isSyncDone.value && computeProgress.value && !computeProgress.value.completed
)

// Progress percentages (0-100)
const syncPercent = computed(() => {
    const p = syncProgress.value
    if (!p || p.total === 0) return 0
    if (p.completed) return 100
    return Math.round((p.current / p.total) * 100)
})

const computePercent = computed(() => {
    const p = computeProgress.value
    if (!p || p.total === 0) return 0
    if (p.completed) return 100
    return Math.round((p.current / p.total) * 100)
})
</script>

<template>
    <Transition name="callout-fade">
        <wa-callout v-if="isVisible" variant="brand" class="startup-callout">
            <wa-spinner slot="icon"></wa-spinner>
            <div class="progress-phases">
                <!-- Phase 1: Syncing sessions from disk -->
                <div class="progress-phase">
                    <div class="phase-header">
                        <span class="phase-label">Syncing sessions data from disk</span>
                        <span v-if="syncProgress" class="phase-counter">
                            {{ syncProgress.current }}/{{ syncProgress.total }}
                        </span>
                    </div>
                    <wa-progress-bar
                        :value="syncPercent"
                        :label="`Syncing sessions: ${syncPercent}%`"
                    ></wa-progress-bar>
                    <p v-if="isSyncActive" class="phase-hint">
                        Sessions will become available once indexing begins in the next step.
                    </p>
                </div>

                <!-- Phase 2: Making sessions usable -->
                <div class="progress-phase">
                    <div class="phase-header">
                        <span class="phase-label">Indexing sessions data</span>
                        <span v-if="isComputeActive" class="phase-counter">
                            {{ computeProgress.current }}/{{ computeProgress.total }}
                        </span>
                    </div>
                    <wa-progress-bar
                        :value="computePercent"
                        :label="`Indexing sessions: ${computePercent}%`"
                    ></wa-progress-bar>
                    <p v-if="isComputeActive" class="phase-hint">
                        Most recent sessions are indexed first and already available to browse.
                    </p>
                </div>
            </div>
        </wa-callout>
    </Transition>
</template>

<style scoped>
.startup-callout {
    margin-bottom: var(--wa-space-l);
    flex-shrink: 0;
}

.progress-phases {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    width: 100%;
}

.phase-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: var(--wa-font-size-m);
    margin-bottom: var(--wa-space-2xs);
}

.phase-label {
    font-weight: 500;
}

.phase-counter {
    color: var(--wa-color-text-quiet);
    font-variant-numeric: tabular-nums;
}

.phase-hint {
    margin: var(--wa-space-s) 0 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

/* Transition for callout disappearance */
.callout-fade-enter-active,
.callout-fade-leave-active {
    transition: opacity 0.4s ease, max-height 0.4s ease;
    max-height: 200px;
    overflow: hidden;
}

.callout-fade-enter-from,
.callout-fade-leave-to {
    opacity: 0;
    max-height: 0;
}
</style>
