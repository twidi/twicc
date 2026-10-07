<script setup>
import { nextTick, onBeforeUnmount, ref } from 'vue'
import { startListReorder } from '../../utils/listReorder'

const props = defineProps({
    index: { type: Number, required: true },
    count: { type: Number, required: true },
    drag: { type: Function, default: null },
    disabled: { type: Boolean, default: undefined },
})
const emit = defineEmits(['reorder'])
const active = ref(false)
const handleRef = ref(null)
const announcement = ref('')
let cancel = () => {}

function reorder(from, to) {
    emit('reorder', from, to)
    announcement.value = `Moved to position ${to + 1} of ${props.count}.`
}

function pointerdown(event) {
    cancel()
    cancel = (props.drag || startListReorder)(event, event.currentTarget, props.drag ? (...args) => emit('reorder', ...args) : reorder, (value) => { active.value = value })
}

function keydown(event) {
    const targets = { ArrowUp: props.index - 1, ArrowDown: props.index + 1, Home: 0, End: props.count - 1 }
    const target = targets[event.key]
    if (target === undefined) return
    event.preventDefault()
    event.stopPropagation()
    cancel()
    if (target >= 0 && target < props.count && target !== props.index) {
        const list = handleRef.value.closest('[data-reorder-list]')
        reorder(props.index, target)
        nextTick(() => {
            const rows = Array.from(list.children).filter((row) => row.hasAttribute('data-reorder-row'))
            rows[target]?.querySelector('.reorder-handle')?.focus()
        })
    }
}

onBeforeUnmount(() => cancel())
</script>

<template>
    <span class="reorder-control">
        <button
            ref="handleRef"
            type="button"
            class="reorder-handle"
            :class="{ active }"
            :disabled="disabled ?? count < 2"
            :aria-label="`Reorder item ${index + 1} of ${count}. Drag or use Up, Down, Home, and End keys.`"
            title="Drag to reorder"
            @pointerdown="pointerdown"
            @keydown="keydown"
            @click.prevent
            @dragstart.prevent
        ><wa-icon name="grip-vertical" family="classic" variant="solid" /></button>
        <span class="reorder-announcement" aria-live="polite">{{ announcement }}</span>
    </span>
</template>

<style scoped>
.reorder-control { display: inline-flex; flex: none; }
.reorder-handle {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    min-height: 36px;
    padding: 0;
    font-size: var(--wa-font-size-m);
    border: none;
    border-radius: var(--wa-border-radius-s);
    background: transparent;
    color: var(--wa-color-text-quiet);
    cursor: grab;
    touch-action: none;
    user-select: none;
    -webkit-user-select: none;
    -webkit-touch-callout: none;
}
.reorder-handle wa-icon { margin: 0; }
.reorder-handle:hover:not(:disabled) { color: var(--wa-color-text-base); background: var(--wa-color-surface-alt); }
.reorder-handle.active { cursor: grabbing; color: var(--wa-color-text-base); }
.reorder-handle:disabled { opacity: 0.25; cursor: default; }
.reorder-handle:focus-visible { outline: var(--wa-focus-ring); outline-offset: var(--wa-focus-ring-offset); }
.reorder-announcement { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
@media (pointer: coarse) {
    .reorder-handle { width: 44px; min-height: 44px; }
}
</style>
