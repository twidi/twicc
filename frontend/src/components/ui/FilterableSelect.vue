<script setup>
// FilterableSelect.vue - Single-choice picker with type-to-filter, an alternative to
// <wa-select> (which cannot filter). A <wa-dropdown> does the menu work: it opens over the
// page (top layer, never clipped), sizes and flips itself, closes on Escape, a click outside
// or a choice. A filter field heads the menu; the options follow in the order of `options`.
import { ref, computed, nextTick } from 'vue'
import { filterSelectOptions } from '../../utils/filterableSelect.js'

const props = defineProps({
    modelValue: { type: String, default: '' },
    // [{ value, label, description?, keywords?, tag?, disabled? }] — displayed in this order.
    // `description` is a quiet second line; `keywords` is extra text the filter matches.
    options: { type: Array, default: () => [] },
    placeholder: { type: String, default: '' },
    size: { type: String, default: 'medium' },
    // The trigger's id, so a <label for> outside points at the field.
    inputId: { type: String, default: null },
    // The field's accessible name, read before the value (the caller shows its own label).
    label: { type: String, default: '' },
    // What the open menu says when it has no option to show.
    emptyText: { type: String, default: 'No match' },
})
const emit = defineEmits(['update:modelValue'])

const dropdownRef = ref(null)
const triggerRef = ref(null)
const filterRef = ref(null)
const query = ref('')
const menuWidth = ref(null)

const selectedOption = computed(() => props.options.find(o => o.value === props.modelValue) || null)
const filtered = computed(() => filterSelectOptions(props.options, query.value))

// The dropdown's own events stay here: a surrounding dialog or card listens to wa-show /
// wa-hide for itself (Web Awesome events bubble), and the choice leaves as update:modelValue.
function onShow(event) {
    if (event.target !== dropdownRef.value) return
    // The menu at least as wide as the field.
    menuWidth.value = triggerRef.value ? `${triggerRef.value.getBoundingClientRect().width}px` : null
}

function onAfterShow(event) {
    if (event.target !== dropdownRef.value) return
    // The dropdown focuses its first option on show: the filter takes the focus instead.
    filterRef.value?.focus()
}

function onAfterHide(event) {
    if (event.target !== dropdownRef.value) return
    query.value = ''
}

function onSelect(event) {
    const value = event.detail?.item?.value
    if (value != null && value !== props.modelValue) emit('update:modelValue', value)
}

function onFilterInput(event) {
    query.value = event.target.value
}

function enabledItems() {
    return [...(dropdownRef.value?.querySelectorAll('wa-dropdown-item:not([disabled])') ?? [])]
}

// The dropdown listens to keys on the document for its own navigation and type-to-select:
// keys typed in the filter stay in the filter. Escape and Tab go through (the dropdown closes).
function onFilterKeydown(event) {
    if (event.key === 'Escape' || event.key === 'Tab') return
    event.stopPropagation()
    if (event.key === 'ArrowDown') {
        // Into the options: the dropdown's arrow keys take over from there.
        event.preventDefault()
        const [first] = enabledItems()
        if (!first) return
        enabledItems().forEach(item => { item.active = item === first })
        first.focus()
    } else if (event.key === 'Enter') {
        // Never submit a surrounding form; pick the first match, like a combobox.
        event.preventDefault()
        const option = filtered.value.find(o => !o.disabled)
        if (!option) return
        if (option.value !== props.modelValue) emit('update:modelValue', option.value)
        dropdownRef.value.open = false
        nextTick(() => triggerRef.value?.focus())
    }
}
</script>

<template>
    <wa-dropdown
        ref="dropdownRef"
        class="filterable-select"
        placement="bottom-start"
        :style="menuWidth ? { '--fs-menu-width': menuWidth } : null"
        @wa-show.stop="onShow"
        @wa-after-show.stop="onAfterShow"
        @wa-hide.stop
        @wa-after-hide.stop="onAfterHide"
        @wa-select.stop="onSelect"
    >
        <wa-button
            ref="triggerRef"
            slot="trigger"
            :id="inputId"
            class="fs-trigger"
            :size="size"
            appearance="outlined"
            with-caret
        >
            <span v-if="label" class="fs-visually-hidden">{{ label }}: </span>
            <span class="fs-trigger-label" :class="{ 'fs-placeholder': !selectedOption }">
                {{ selectedOption ? selectedOption.label : placeholder }}
            </span>
        </wa-button>

        <div class="fs-filter">
            <wa-input
                ref="filterRef"
                size="small"
                :placeholder="$t('common.select.filterPlaceholder')"
                autocomplete="off"
                :label="label ? `Filter ${label.toLowerCase()}` : 'Filter'"
                class="fs-filter-input"
                :value.prop="query"
                @input="onFilterInput"
                @keydown="onFilterKeydown"
            >
                <wa-icon slot="start" name="magnifying-glass"></wa-icon>
            </wa-input>
        </div>

        <wa-dropdown-item
            v-for="option in filtered"
            :key="option.value"
            :value="option.value"
            :disabled="option.disabled"
        >
            <wa-icon
                slot="icon"
                name="check"
                :class="{ 'fs-check-hidden': option.value !== modelValue }"
            ></wa-icon>
            <span class="fs-option-label">{{ option.label }}</span>
            <span v-if="option.description" class="fs-option-description">{{ option.description }}</span>
            <span v-if="option.tag" slot="details" class="fs-option-tag">{{ option.tag }}</span>
        </wa-dropdown-item>

        <div v-if="filtered.length === 0" class="fs-empty">{{ emptyText }}</div>
    </wa-dropdown>
</template>

<style scoped>
.filterable-select {
    display: block;
}

.filterable-select::part(menu) {
    min-width: var(--fs-menu-width, auto);
    max-width: min(32rem, calc(100vw - 2rem));
}

.fs-trigger {
    width: 100%;
}

.fs-trigger::part(base) {
    justify-content: space-between;
}

.fs-trigger::part(label) {
    flex: 1;
    min-width: 0;
    text-align: start;
}

.fs-trigger-label {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.fs-placeholder {
    color: var(--wa-color-text-quiet);
}

.fs-filter {
    padding: var(--wa-space-2xs);
}

/* The filter's accessible name stays off screen. */
.fs-filter-input::part(form-control-label),
.fs-visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
}

.fs-check-hidden {
    visibility: hidden;
}

.fs-option-label,
.fs-option-description {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.fs-option-description {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.fs-option-tag {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.fs-empty {
    padding: var(--wa-space-2xs) var(--wa-space-s);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}
</style>
