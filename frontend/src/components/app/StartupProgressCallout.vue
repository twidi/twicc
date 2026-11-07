<script setup>
import { resolveUiMessage } from '../../i18n/uiMessages.js'
import { computed } from 'vue'
import { useDataStore } from '../../stores/data'

const store = useDataStore()

const syncProgress = computed(() => store.initialSyncProgress)
const computeProgress = computed(() => store.backgroundComputeProgress)
const searchIndexingProgress = computed(() => store.searchIndexProgress)

// Show callout if any phase is active (not null and not completed)
const isVisible = computed(() => {
    return (syncProgress.value && !syncProgress.value.completed) ||
           (computeProgress.value && !computeProgress.value.completed) ||
           (searchIndexingProgress.value && !searchIndexingProgress.value.completed)
})

// Sync is actively running (not yet completed)
const isSyncActive = computed(() =>
    syncProgress.value && !syncProgress.value.completed
)

// Compute is actively running for at least one provider. Decoupled from
// initial-sync completion: each provider's compute starts as soon as its
// own initial sync finishes, so the hint surfaces while another provider
// is still syncing.
const isComputeActive = computed(() =>
    computeProgress.value && !computeProgress.value.completed
)

// Search is actively running (not yet completed)
const isSearchIndexingActive = computed(() =>
    searchIndexingProgress.value && !searchIndexingProgress.value.completed
)

// Progress percentages (0-100)
// Note: completed must be checked before total === 0, because a phase with
// nothing to do broadcasts {current: 0, total: 0, completed: true}.
const syncPercent = computed(() => {
    const p = syncProgress.value
    if (!p) return 0
    if (p.completed) return 100
    if (p.total === 0) return 0
    return Math.round((p.current / p.total) * 100)
})

const computePercent = computed(() => {
    const p = computeProgress.value
    if (!p) return 0
    if (p.completed) return 100
    if (p.total === 0) return 0
    return Math.round((p.current / p.total) * 100)
})

const searchIndexingPercent = computed(() => {
    const p = searchIndexingProgress.value
    if (!p) return 0
    if (p.completed) return 100
    if (p.total === 0) return 0
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
                        <span class="phase-label">{{ $t('app.startup.syncing') }}</span>
                        <span v-if="syncProgress" class="phase-counter">
                            {{ syncProgress.current }}/{{ syncProgress.total }}
                        </span>
                    </div>
                    <wa-progress-bar
                        :value="syncPercent"
                        :label="$t('app.startup.syncProgress', { percent: syncPercent })"
                    ></wa-progress-bar>
                    <p v-if="isSyncActive" class="phase-hint">
                        {{ $t('app.startup.syncHint') }}
                    </p>
                </div>

                <!-- Phase 2: Making sessions usable -->
                <div class="progress-phase">
                    <div class="phase-header">
                        <span class="phase-label">{{ $t('app.startup.indexingSessions') }}</span>
                        <span v-if="computeProgress" class="phase-counter">
                            {{ computeProgress.current }}/{{ computeProgress.total }}
                        </span>
                    </div>
                    <wa-progress-bar
                        :value="computePercent"
                        :label="$t('app.startup.computeProgress', { percent: computePercent })"
                    ></wa-progress-bar>
                    <p v-if="isComputeActive" class="phase-hint">
                        {{ $t('app.startup.indexingHint') }}
                    </p>
                    <p
                        v-for="detail in (computeProgress?.details || [])"
                        :key="detail"
                        class="phase-detail"
                    >
                        {{ resolveUiMessage(detail) }}
                    </p>
                </div>

                <!-- Phase 3: Building search index -->
                <div class="progress-phase">
                    <div class="phase-header">
                        <span class="phase-label">{{ $t('app.startup.buildingSearchIndex') }}</span>
                        <span v-if="searchIndexingProgress" class="phase-counter">
                            {{ searchIndexingProgress.current }}/{{ searchIndexingProgress.total }}
                        </span>
                    </div>
                    <wa-progress-bar
                        :value="searchIndexingPercent"
                        :label="$t('app.startup.searchProgress', { percent: searchIndexingPercent })"
                    ></wa-progress-bar>
                    <p v-if="isSearchIndexingActive" class="phase-hint">
                        {{ $t('app.startup.searchHint') }}
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

.phase-detail {
    margin: var(--wa-space-2xs) 0 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-variant-numeric: tabular-nums;
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
