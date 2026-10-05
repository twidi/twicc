<script setup>
// A session's annotations (free-form key/value): ONE line of tags that never wraps (tags that do not
// fit are hidden), and a chevron button that is always there. The button opens a popover listing ALL
// annotations as a tree (dotted keys become levels). When tags are hidden, the button shows their count.
import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import AnnotationTreeLevel from './AnnotationTreeLevel.vue'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'
import { annotationEntries, annotationValueText, buildAnnotationTree } from '../../utils/orchestrationView'

const props = defineProps({
    annotations: { type: Object, required: true },
})

const uid = useId()
const buttonId = `${uid}-ann-button`

const entries = computed(() => annotationEntries(props.annotations))
const tree = computed(() => buildAnnotationTree(props.annotations))

// How many tags fit on the line: measured on the rendered row. Hidden tags stay in the layout
// (``visibility: hidden``) so the measurement is stable.
const tagsEl = ref(null)
const fitCount = ref(entries.value.length)
const hiddenCount = computed(() => entries.value.length - fitCount.value)

// Hysteresis: the count only changes when the row width does. If every tag would fit without the button but
// not with it, one tag can stay hidden until the next resize. Accepted.
function measure() {
    const el = tagsEl.value
    if (!el) return
    const limit = el.clientWidth
    let fit = 0
    for (const child of el.children) {
        if (child.offsetLeft + child.offsetWidth <= limit) fit += 1
        else break
    }
    fitCount.value = fit
}

onMounted(measure)
useResizeObserver(tagsEl, measure)
watch(entries, () => nextTick(measure))
</script>

<template>
    <div class="oann">
        <div ref="tagsEl" class="oann-tags">
            <span
                v-for="([key, value], index) in entries"
                :key="key"
                class="oann-tag"
                :class="{ 'is-hidden': index >= fitCount }"
            >
                <span class="oann-key">{{ key }}</span>
                <span class="oann-value">{{ annotationValueText(value) }}</span>
            </span>
        </div>
        <button :id="buttonId" type="button" class="oann-button" aria-label="Show all annotations">
            <span v-if="hiddenCount > 0" class="oann-count">+{{ hiddenCount }}</span>
            <wa-icon auto-width name="chevron-down"></wa-icon>
        </button>
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
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    min-width: 0;
}

.oann-tags {
    position: relative;
    flex: 1;
    min-width: 0;
    display: flex;
    flex-wrap: nowrap;
    gap: var(--wa-space-2xs);
    overflow: hidden;
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

.oann-tag.is-hidden {
    visibility: hidden;
}

.oann-key {
    flex: none;
    font-weight: 500;
    color: var(--wa-color-text-quiet);
}

.oann-value {
    min-width: 0;
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
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-3xs);
    padding: 0.24rem 0.5rem;
    border-radius: 999px;
    font: inherit;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-on-quiet);
    background: var(--wa-color-brand-fill-quiet);
    border: 1px solid var(--wa-color-brand-border-quiet);
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
