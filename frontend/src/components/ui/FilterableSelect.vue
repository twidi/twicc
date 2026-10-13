<script setup>
// FilterableSelect.vue - Single-choice picker with type-to-filter, an
// alternative to <wa-select> (which cannot filter). The input shows the
// selected label while closed and becomes the filter field while open; the
// option list is rendered inline below it, in the order given by `options`.
import { ref, computed, watch, nextTick } from 'vue'

const props = defineProps({
    modelValue: { type: String, default: '' },
    // [{ value, label, tag?, disabled? }] — displayed in this order.
    options: { type: Array, default: () => [] },
    placeholder: { type: String, default: '' },
    size: { type: String, default: 'medium' },
})
const emit = defineEmits(['update:modelValue'])

const inputRef = ref(null)
const listRef = ref(null)
const isOpen = ref(false)
const query = ref('')
const activeIndex = ref(-1)

const selectedOption = computed(() => props.options.find(o => o.value === props.modelValue) || null)

const filtered = computed(() => {
    const q = query.value.trim().toLowerCase()
    if (!q) return props.options
    return props.options.filter(o => o.label.toLowerCase().includes(q))
})

// While open the input is the filter field; otherwise it shows the selection.
const inputValue = computed(() => (isOpen.value ? query.value : selectedOption.value?.label || ''))

function firstEnabledIndex() {
    return filtered.value.findIndex(o => !o.disabled)
}

function open() {
    if (isOpen.value) return
    isOpen.value = true
    query.value = ''
    activeIndex.value = Math.max(
        filtered.value.findIndex(o => o.value === props.modelValue && !o.disabled),
        firstEnabledIndex(),
    )
    nextTick(scrollActiveIntoView)
}

function close() {
    isOpen.value = false
    query.value = ''
}

function choose(option) {
    if (!option || option.disabled) return
    emit('update:modelValue', option.value)
    close()
}

// The active row follows the filter: reset to the first enabled match.
watch(query, () => {
    activeIndex.value = firstEnabledIndex()
    nextTick(scrollActiveIntoView)
})

function scrollActiveIntoView() {
    listRef.value?.querySelector('.fs-option-active')?.scrollIntoView({ block: 'nearest' })
}

function move(step) {
    const list = filtered.value
    if (!list.some(o => !o.disabled)) return
    let i = activeIndex.value
    do {
        i = (i + step + list.length) % list.length
    } while (list[i].disabled)
    activeIndex.value = i
    nextTick(scrollActiveIntoView)
}

function onInput(event) {
    if (event.target !== inputRef.value) return
    if (!isOpen.value) isOpen.value = true
    query.value = event.target.value
}

function onKeydown(event) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        if (!isOpen.value) open()
        else move(event.key === 'ArrowDown' ? 1 : -1)
    } else if (event.key === 'Enter' && isOpen.value) {
        // Never submit the surrounding form while the list is open.
        event.preventDefault()
        choose(filtered.value[activeIndex.value])
    } else if (event.key === 'Escape' && isOpen.value) {
        // Close the list only — not the surrounding dialog.
        event.preventDefault()
        event.stopPropagation()
        close()
    }
}
</script>

<template>
    <div class="filterable-select">
        <wa-input
            ref="inputRef"
            :size="size"
            :placeholder="selectedOption ? selectedOption.label : placeholder"
            :value.prop="inputValue"
            autocomplete="off"
            @input="onInput"
            @focus="open"
            @click="open"
            @blur="close"
            @keydown="onKeydown"
        >
            <wa-icon slot="end" :name="isOpen ? 'chevron-up' : 'chevron-down'" auto-width></wa-icon>
        </wa-input>
        <div v-if="isOpen" ref="listRef" class="fs-list" role="listbox">
            <button
                v-for="(option, index) in filtered"
                :key="option.value"
                type="button"
                class="fs-option"
                :class="{
                    'fs-option-active': index === activeIndex,
                    'fs-option-selected': option.value === modelValue,
                }"
                :disabled="option.disabled"
                role="option"
                :aria-selected="option.value === modelValue"
                tabindex="-1"
                @mousedown.prevent
                @click="choose(option)"
                @mouseenter="!option.disabled && (activeIndex = index)"
            >
                <span class="fs-option-label">{{ option.label }}</span>
                <span v-if="option.tag" class="fs-option-tag">{{ option.tag }}</span>
            </button>
            <div v-if="filtered.length === 0" class="fs-empty">No match</div>
        </div>
    </div>
</template>

<style scoped>
.filterable-select {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.fs-list {
    display: flex;
    flex-direction: column;
    max-height: 200px;
    overflow-y: auto;
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
}

.fs-option {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    background: none;
    border: none;
    height: auto;
    cursor: pointer;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-base);
    text-align: left;
}

.fs-option-active:not(:disabled) {
    background: var(--wa-color-surface-alt);
}

.fs-option-selected {
    font-weight: var(--wa-font-weight-bold);
}

.fs-option:disabled {
    cursor: not-allowed;
    color: var(--wa-color-text-quiet);
}

.fs-option-label {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.fs-option-tag {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.fs-empty {
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}
</style>
