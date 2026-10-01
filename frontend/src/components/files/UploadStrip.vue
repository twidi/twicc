<script setup>
// The in-tab upload indicator (spec §6.7): a compact strip at the top of a
// Files / Artifacts panel, one line per upload that belongs to the panel
// (same `origin.panel` and `origin.key`). Nothing when the panel has none.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useUploadsStore } from '../../stores/uploads'
import { displayTargetDir } from '../../utils/uploads/paths'
import { entryPercent, takePickedFiles } from '../../utils/uploads/display'
import { originKey } from '../../utils/uploads/rules'

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

// The controller drops an entry the moment its upload completes. The strip keeps a finished
// line for a moment, its icon turned into a check, so the end of an upload is seen before
// the strip folds. Fed by the store's completion event, for this panel's uploads only.
const DONE_VISIBLE_MS = 1800
const finished = ref([])
const doneTimers = new Set()
let unsubscribeCompleted = null

onMounted(() => {
    unsubscribeCompleted = uploads.onCompleted(record => {
        if (!props.origin || !record.origin || originKey(record.origin) !== originKey(props.origin)) return
        // Only a line that was live in this panel: the controller replays `completed` for the
        // uploads already finished on the server at each reload, which must not bring lines back.
        // (The controller emits before it drops the entry, so the live line is still there.)
        const live = uploads.entriesForOrigin(props.origin).find(e => e.key === record.id || (record.client_id && e.clientId === record.client_id))
        if (!live) return
        const line = {
            done: true,
            entry: { key: `done:${record.id}`, filename: record.filename, targetDir: record.target_dir },
            target: displayTargetDir(record.target_dir, props.rootPath),
            percent: 100,
            actions: { text: null, error: null, buttons: [] },
        }
        finished.value = [...finished.value, line]
        // By key, not by identity: the ref's array hands out proxies, never the raw object.
        const timer = setTimeout(() => {
            doneTimers.delete(timer)
            finished.value = finished.value.filter(l => l.entry.key !== line.entry.key)
        }, DONE_VISIBLE_MS)
        doneTimers.add(timer)
    })
})

onBeforeUnmount(() => {
    unsubscribeCompleted?.()
    unsubscribeCompleted = null
    for (const timer of doneTimers) clearTimeout(timer)
    doneTimers.clear()
})

const lines = computed(() => {
    if (!props.origin) return []
    const live = uploads.entriesForOrigin(props.origin).map(entry => ({
        entry,
        done: false,
        target: displayTargetDir(entry.targetDir, props.rootPath),
        percent: entryPercent(entry),
        actions: uploads.entryActions(entry),
    }))
    return [...live, ...finished.value]
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
    <Transition name="upload-strip">
        <div v-if="lines.length" class="upload-strip">
            <div class="upload-strip-inner">
                <div class="upload-lines">
                    <div
                        v-for="line in lines"
                        :key="line.entry.key"
                        class="upload-line"
                        :class="{ 'upload-line--done': line.done }"
                    >
                        <wa-icon :name="line.done ? 'check' : 'upload'" class="upload-icon" :class="{ 'upload-icon--done': line.done }" aria-hidden="true"></wa-icon>
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
            </div>
        </div>
    </Transition>
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
/* The strip unfolds when the first upload starts
   and folds when the last one ends: the row track grows from 0fr (a grid track animates
   where `height: auto` cannot), with a fade. The scroll box is the inner element, so the
   padding sits inside the part that folds. */
.upload-strip {
    --upload-line-height: 1.9rem;
    flex: 0 0 auto;
    display: grid;
    grid-template-rows: 1fr;
    border-bottom: 1px solid color-mix(in oklab, var(--glow-accent) 18%, var(--wa-color-surface-border));
    background: var(--wa-color-surface-lowered);
    font-size: var(--wa-font-size-s);
}

.upload-strip-inner {
    min-height: 0;
    max-height: calc(4 * var(--upload-line-height) + 2 * var(--wa-space-2xs));
    overflow: auto;
}

.upload-lines {
    padding: var(--wa-space-2xs) var(--wa-space-s);
}

.upload-strip-enter-active,
.upload-strip-leave-active {
    transition:
        grid-template-rows var(--motion-dur-3) var(--motion-ease-out-height),
        border-bottom-width var(--motion-dur-3) var(--motion-ease-out-height),
        opacity var(--motion-dur-2) ease-in-out;
}
.upload-strip-enter-from,
.upload-strip-leave-to {
    grid-template-rows: 0fr;
    border-bottom-width: 0;
    opacity: 0;
}
/* No scrollbar flash while the box is smaller than its content. */
.upload-strip-enter-active .upload-strip-inner,
.upload-strip-leave-active .upload-strip-inner {
    overflow: hidden;
}

/* A line rises in (the movement × --motion-amount: reduced motion keeps the fade). */
.upload-line {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-height: var(--upload-line-height);
    animation: upload-line-in var(--motion-dur-2) var(--motion-ease-out) backwards;
}
@keyframes upload-line-in {
    from {
        opacity: 0;
        translate: 0 calc(0.375rem * var(--motion-amount));
    }
}

/* The upload icon of the tab status (UploadTabStatus.vue), in the text colour. It breathes like
   the unread eye while the file goes up (opacity only: kept under reduced motion), then gives
   way to a check in the green of a finished turn. */
.upload-icon {
    flex: 0 0 auto;
    font-size: var(--wa-font-size-s);
    animation: motion-status-pulse 2.4s ease-in-out infinite;
}
.upload-icon--done {
    color: var(--wa-color-success-60);
    animation: none;
}
/* The finished line takes the place of the live one: no second entrance. */
.upload-line--done {
    animation: none;
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

/* The look of the Tasks tab's progress bar (TaskPane.vue), in the accent colour: a neutral
   track and a lit gradient fill with a soft glow. The track does not clip (Web Awesome's
   overflow: hidden would cut the glow), so the fill carries its own rounded ends. The fill's
   width animation is Web Awesome's own. */
.upload-progress {
    flex: 0 0 5rem;
    --track-height: 0.375rem;
    --track-color: var(--progress-track);
}
.upload-progress::part(base) {
    overflow: visible;
}
.upload-progress::part(indicator) {
    background: linear-gradient(90deg, oklch(from var(--wa-color-brand-60) calc(l + 0.08) c h), var(--wa-color-brand-60));
    border-radius: var(--wa-border-radius-pill);
    box-shadow: 0 0 0.25rem color-mix(in oklab, var(--wa-color-brand-60) 40%, transparent);
}
/* Reduced motion: the bar snaps (as in the Tasks tab). After the rule above. */
@media (prefers-reduced-motion: reduce) {
    .upload-progress::part(indicator) {
        transition: none;
    }
}

.upload-percent {
    flex: 0 0 auto;
    min-width: 4ch;
    text-align: end;
    font-variant-numeric: tabular-nums;
    color: var(--wa-color-brand);
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

/* Compact buttons: a small wa-button is taller than a strip line, which
   would fit only about three lines in the max-height. Its shadow height,
   line height and icon-only width all derive from this property. */
.upload-buttons wa-button {
    --wa-form-control-height: 1.5rem;
    --wa-form-control-padding-inline: var(--wa-space-xs);
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
