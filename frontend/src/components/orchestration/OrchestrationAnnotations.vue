<script setup>
// A session's annotations (free-form key/value): tags that wrap onto up to MAX_LINES lines (tags that do not
// fit in those lines are hidden), followed in the same flow by a chevron button that is always there. The
// button opens a popover listing ALL annotations as a tree (dotted keys become levels). When entry tags are
// hidden, the button shows their count.
import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import AnnotationTreeLevel from './AnnotationTreeLevel.vue'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'
import {
    annotationValueText, buildAnnotationTree, flattenAnnotations, lineIndices, nextFitCount, splitCommonPrefix,
} from '../../utils/orchestrationView'

const MAX_LINES = 3

const props = defineProps({
    annotations: { type: Object, required: true },
})

const uid = useId()
const buttonId = `${uid}-ann-button`

// Card tags: leaf entries, with their common key prefix shown once as a separate tag.
const split = computed(() => splitCommonPrefix(flattenAnnotations(props.annotations)))
const prefix = computed(() => split.value.prefix)
const entries = computed(() => split.value.entries)
const tree = computed(() => buildAnnotationTree(props.annotations))

// How many entry tags are displayed: measured on the rendered flow. Hidden entries are ``display: none`` so the
// button, which follows the tags in the same flow, sits right after the last displayed tag.
const tagsEl = ref(null)
const fitCount = ref(entries.value.length)
const hiddenCount = computed(() => entries.value.length - fitCount.value)

// Lay out all entries, then hide entries until the button (the last flow item, showing ``+N``) is within
// MAX_LINES. Hiding changes the width of ``+N`` and so the wrapping: re-measure after every step. The count
// only decreases within a run, so the loop ends after at most one step per entry; ``run`` drops a stale run.
let run = 0
async function measure() {
    const current = ++run
    fitCount.value = entries.value.length
    await nextTick()
    for (let step = 0; step <= entries.value.length; step++) {
        const el = tagsEl.value
        if (!el || current !== run) return
        const button = el.querySelector('.oann-button')
        const shown = [...el.querySelectorAll('.oann-entry:not(.is-hidden)')]
        // The prefix tag (when present) is the first flow item: always displayed, never counted.
        const prefixEl = el.querySelector('.oann-prefix')
        const flow = [...(prefixEl ? [prefixEl] : []), ...shown, button]
        const lines = lineIndices(flow.map((child) => child.offsetTop))
        const next = nextFitCount({
            entryLines: lines.slice(prefixEl ? 1 : 0, -1),
            chevronLine: lines[lines.length - 1],
            maxLines: MAX_LINES,
        })
        if (next === shown.length) return
        fitCount.value = next
        await nextTick()
    }
}

// Only a width change re-measures: hiding tags changes the height, which must not retrigger a measure.
let lastWidth = -1
function onResize() {
    const width = tagsEl.value?.clientWidth ?? -1
    if (width === lastWidth) return
    lastWidth = width
    measure()
}

onMounted(() => {
    lastWidth = tagsEl.value?.clientWidth ?? -1
    measure()
})
useResizeObserver(tagsEl, onResize)
watch([entries, prefix], measure)
</script>

<template>
    <div class="oann">
        <div ref="tagsEl" class="oann-tags">
            <span v-if="prefix" class="oann-tag oann-prefix">{{ prefix }}</span>
            <span
                v-for="(entry, index) in entries"
                :key="`${index}:${entry.fullKey}`"
                class="oann-tag oann-entry"
                :class="{ 'is-hidden': index >= fitCount }"
            >
                <span class="oann-key">{{ entry.key }}</span>
                <span class="oann-value">{{ annotationValueText(entry.value) }}</span>
            </span>
            <button :id="buttonId" type="button" class="oann-button" aria-label="Show all annotations">
                <span v-if="hiddenCount > 0" class="oann-count">+{{ hiddenCount }}</span>
                <wa-icon auto-width name="chevron-down"></wa-icon>
            </button>
        </div>
        <wa-popover v-popover-focus-fix :for="buttonId" placement="bottom-start" class="oann-popover">
            <div class="oann-popover-body">
                <div class="oann-popover-title">Annotations</div>
                <AnnotationTreeLevel :nodes="tree" />
            </div>
        </wa-popover>
    </div>
</template>

<style scoped>
.oann {
    min-width: 0;
}

.oann-tags {
    min-width: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    /* Row gap half the column gap: wrapped tag lines sit closer than tags side by side. */
    gap: calc(var(--wa-space-2xs) / 2) var(--wa-space-2xs);
}

.oann-tag {
    flex: none;
    max-width: 100%;
    display: inline-flex;
    gap: var(--wa-space-2xs);
    padding: 0.24rem 0.5rem;
    border-radius: 999px;
    font-size: var(--wa-font-size-xs);
    line-height: 1;
    background: color-mix(in oklab, var(--wa-color-neutral-fill-quiet) 70%, transparent);
    border: 1px solid color-mix(in oklab, var(--wa-color-surface-border) 80%, transparent);
}

.oann-prefix {
    font-weight: 500;
    color: var(--wa-color-brand-on-quiet);
    background: var(--wa-color-brand-fill-quiet);
    border-color: var(--wa-color-brand-border-quiet);
}

.oann-tag.is-hidden {
    display: none;
}

.oann-key {
    flex: none;
    font-weight: 500;
    color: var(--wa-color-text-quiet);
}

.oann-value {
    min-width: 0;
    max-width: 10rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.oann-button {
    flex: none;
    appearance: none;
    /* Web Awesome's native.css forces a form-control height and line-height on every <button>. */
    height: auto;
    line-height: 1;
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-3xs);
    padding: 0.24rem 0.5rem;
    border: 1px solid var(--wa-color-brand-border-quiet);
    border-radius: 999px;
    background: var(--wa-color-brand-fill-quiet);
    cursor: pointer;
    font: inherit;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-on-quiet);
}

.oann-button:hover {
    background: var(--wa-color-brand-fill-normal);
}

.oann-popover {
    --max-width: min(24rem, 90vw);
}

.oann-popover-body {
    max-height: 50vh;
    overflow: auto;
    font-size: var(--wa-font-size-s);
}

.oann-popover-title {
    margin-bottom: var(--wa-space-2xs);
    font-size: var(--wa-font-size-2xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--wa-color-text-quiet);
}
</style>
