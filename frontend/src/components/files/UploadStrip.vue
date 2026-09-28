<script setup>
// The in-tab upload indicator (spec §6.7): a compact strip at the top of a
// Files / Artifacts panel, one line per upload that belongs to the panel
// (same `origin.panel` and `origin.key`). Nothing when the panel has none.
import { computed, ref } from 'vue'
import { useUploadsStore } from '../../stores/uploads'
import { displayTargetDir } from '../../utils/uploads/paths'
import { entryPercent, takePickedFiles } from '../../utils/uploads/display'

const props = defineProps({
    /** `{ panel, key }` of the panel (spec §6.7). */
    origin: {
        type: Object,
        default: null,
    },
    /** Absolute path of the panel's tree root: target dirs show relative to it. */
    rootPath: {
        type: String,
        default: null,
    },
})

const uploads = useUploadsStore()

const lines = computed(() => {
    if (!props.origin) return []
    return uploads.entriesForOrigin(props.origin).map(entry => ({
        entry,
        target: displayTargetDir(entry.targetDir, props.rootPath),
        percent: entryPercent(entry),
        actions: uploads.entryActions(entry),
    }))
})

// ─── Resume (spec §6.8) ─────────────────────────────────────────────────────

const resumeInputRef = ref(null)
let resumeKey = null

/**
 * Open the single-file picker for a stalled entry. The `click()` stays
 * synchronous (no `await` before it): iOS opens a picker only inside the
 * user gesture.
 */
function pickForResume(key) {
    const input = resumeInputRef.value
    if (!input) return
    resumeKey = key
    input.click()
}

function onResumeInputChange(event) {
    const [file] = takePickedFiles(event.target)
    const key = resumeKey
    resumeKey = null
    if (!file || !key) return
    uploads.resume(key, file)
}
</script>

<template>
    <div v-if="lines.length" class="upload-strip">
        <div
            v-for="line in lines"
            :key="line.entry.key"
            class="upload-line"
        >
            <div class="upload-names">
                <span class="upload-name" :title="line.entry.filename">{{ line.entry.filename }}</span>
                <span class="upload-target" :title="line.entry.targetDir">{{ line.target }}</span>
            </div>
            <wa-progress-bar
                class="upload-progress"
                :value="line.percent"
                :label="`Upload of ${line.entry.filename}: ${line.percent}%`"
            ></wa-progress-bar>
            <span class="upload-percent">{{ line.percent }}%</span>
            <span v-if="line.actions.text" class="upload-state">{{ line.actions.text }}</span>
            <span v-if="line.actions.error" class="upload-error" :title="line.actions.error">{{ line.actions.error }}</span>
            <div class="upload-buttons">
                <wa-button
                    v-if="line.actions.buttons.includes('retry')"
                    size="small"
                    appearance="plain"
                    @click="uploads.retry(line.entry.key)"
                >Retry</wa-button>
                <wa-button
                    v-if="line.actions.buttons.includes('retryFinalization')"
                    size="small"
                    appearance="plain"
                    @click="uploads.retryFinalization(line.entry.key)"
                >Retry</wa-button>
                <wa-button
                    v-if="line.actions.buttons.includes('resume')"
                    size="small"
                    appearance="plain"
                    @click="pickForResume(line.entry.key)"
                >Resume</wa-button>
                <wa-button
                    v-if="line.actions.buttons.includes('cancel')"
                    size="small"
                    appearance="plain"
                    class="upload-cancel"
                    :aria-label="`Cancel the upload of ${line.entry.filename}`"
                    @click="uploads.cancel(line.entry.key)"
                ><wa-icon name="xmark" label="Cancel"></wa-icon></wa-button>
            </div>
        </div>
    </div>
    <!-- Outside the v-if: the picker may still be open when the last line goes. -->
    <input
        ref="resumeInputRef"
        type="file"
        class="resume-input"
        tabindex="-1"
        aria-hidden="true"
        @change="onResumeInputChange"
    >
</template>

<style scoped>
.upload-strip {
    --upload-line-height: 1.9rem;
    flex: 0 0 auto;
    max-height: calc(4 * var(--upload-line-height) + 2 * var(--wa-space-2xs));
    overflow: auto;
    padding: var(--wa-space-2xs) var(--wa-space-s);
    border-bottom: 1px solid var(--wa-color-surface-border);
    background: var(--wa-color-surface-lowered);
    font-size: var(--wa-font-size-s);
}

.upload-line {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-height: var(--upload-line-height);
}

.upload-names {
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-xs);
    overflow: hidden;
    white-space: nowrap;
}

.upload-name {
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 0 1 auto;
    min-width: 3ch;
}

.upload-target {
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 0 1 auto;
    min-width: 0;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.upload-progress {
    flex: 0 0 5rem;
    --track-height: 0.375rem;
}

.upload-percent {
    flex: 0 0 auto;
    min-width: 4ch;
    text-align: end;
    font-variant-numeric: tabular-nums;
    color: var(--wa-color-text-quiet);
}

.upload-state {
    flex: 0 0 auto;
    color: var(--wa-color-text-quiet);
}

.upload-error {
    flex: 0 1 auto;
    min-width: 0;
    max-width: 40%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--wa-color-danger-on-quiet);
}

.upload-buttons {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
}

/* Hidden single-file input of *Resume*. Visually hidden rather than
   `display: none`: some mobile browsers refuse a programmatic click() on a
   non-rendered file input. */
.resume-input {
    position: fixed;
    top: 0;
    left: 0;
    width: 1px;
    height: 1px;
    opacity: 0;
    overflow: hidden;
    pointer-events: none;
    clip-path: inset(50%);
}
</style>
