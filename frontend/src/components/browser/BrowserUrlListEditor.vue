<script setup>
import ReorderHandle from '../ui/ReorderHandle.vue'
// Editable list of saved Browser-pane URL entries ({ url, label?, default? }),
// shared by ProjectEditDialog and WorkspaceManageDialog. The component keeps
// its own editable rows (seeded from the `entries` prop); the owner dialog
// calls getEntries() at save time to obtain the validated canonical list (or
// a user-facing error), and reset() when it (re)opens.
import { ref, useId, watch } from 'vue'
import { normalizeBrowserUrl } from '../../utils/browserUrl'

const props = defineProps({
    entries: { type: Array, default: () => [] },
})

const radioName = `browser-url-default-${useId()}`
const rows = ref([])

function reset() {
    rows.value = (props.entries || []).map((e) => ({
        url: e.url || '',
        label: e.label || '',
        isDefault: !!e.default,
    }))
}

watch(() => props.entries, reset, { immediate: true })

function addRow() {
    rows.value.push({ url: '', label: '', isDefault: rows.value.length === 0 })
}

function removeRow(index) {
    rows.value.splice(index, 1)
}

function moveRow(index, target) {
    if (target < 0 || target >= rows.value.length) return
    const [row] = rows.value.splice(index, 1)
    rows.value.splice(target, 0, row)
}

function setDefault(index) {
    rows.value.forEach((row, i) => {
        row.isDefault = i === index
    })
}

/**
 * Validate + canonicalize the rows. Empty-URL rows are dropped. Returns
 * `{ entries }` on success or `{ error }` with a user-facing message.
 */
function getEntries() {
    const entries = []
    const seen = new Set()
    for (const row of rows.value) {
        const raw = row.url.trim()
        if (!raw) continue
        const url = normalizeBrowserUrl(raw)
        if (!url) return { error: `Browser URL must be a valid http(s) URL: "${raw}"` }
        if (seen.has(url)) return { error: `Duplicate browser URL: ${url}` }
        seen.add(url)
        const entry = { url }
        const label = row.label.trim()
        if (label) entry.label = label
        if (row.isDefault) entry.default = true
        entries.push(entry)
    }
    return { entries }
}

defineExpose({ getEntries, reset })
</script>

<template>
    <div class="browser-url-list" data-reorder-list>
        <div v-for="(row, index) in rows" :key="index" class="browser-url-row" data-reorder-row>
            <ReorderHandle :index="index" :count="rows.length" @reorder="moveRow" />
            <input
                type="radio"
                class="browser-url-default"
                :name="radioName"
                :checked="row.isDefault"
                title="Home default"
                @change="setDefault(index)"
            />
            <wa-input
                class="browser-url-url"
                size="small"
                autocomplete="off"
                placeholder="e.g. http://localhost:3000"
                :value="row.url"
                @input="row.url = $event.target.value"
            />
            <wa-input
                class="browser-url-label"
                size="small"
                autocomplete="off"
                maxlength="100"
                placeholder="Label (optional)"
                :value="row.label"
                @input="row.label = $event.target.value"
            />
            <span class="browser-url-actions">
                <button type="button" class="browser-url-action browser-url-action--danger" title="Remove" @click="removeRow(index)">
                    <wa-icon name="trash"></wa-icon>
                </button>
            </span>
        </div>
        <wa-button size="small" appearance="outlined" class="browser-url-add" @click="addRow">
            <wa-icon slot="start" name="plus"></wa-icon>
            Add URL
        </wa-button>
    </div>
</template>

<style scoped>
.browser-url-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.browser-url-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.browser-url-default {
    margin: 0;
    flex: none;
    accent-color: var(--wa-color-brand-fill-loud);
    cursor: pointer;
}

.browser-url-url {
    flex: 3;
    min-width: 0;
}

.browser-url-label {
    flex: 2;
    min-width: 0;
}

.browser-url-actions {
    display: flex;
    align-items: center;
    flex: none;
}

/* Native buttons: WA's native.css gives them form-control height — reset. */
.browser-url-action {
    display: inline-flex;
    align-items: center;
    height: auto;
    padding: var(--wa-space-3xs);
    border: none;
    background: none;
    cursor: pointer;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

.browser-url-action:disabled {
    opacity: 0.35;
    cursor: default;
}

.browser-url-action:not(:disabled):hover {
    color: var(--wa-color-text-normal);
}

.browser-url-action--danger:not(:disabled):hover {
    color: var(--wa-color-danger-fill-loud);
}

.browser-url-add {
    align-self: flex-start;
}
</style>
