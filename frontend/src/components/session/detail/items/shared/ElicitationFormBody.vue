<script setup>
// ElicitationFormBody.vue (shared) — body sub-component for an
// ``elicitationForm`` pending request (request_type ``ask_user_question``,
// mode ``form``). Used by BOTH providers' PendingRequestBody: Codex (untagged
// ``mcpServer/elicitation/request`` — the ``_meta``-tagged tool-call variant
// routes to McpToolCallApprovalBody instead) and Claude Code (the
// ``subtype: "elicitation"`` control request, normalised backend-side to the
// same tool_input keys by the elicitation bridge).
//
// Wire params (tool_input): { serverName, mode: 'form', message,
//   requestedSchema: { type: 'object', properties: {...}, required: [...] } }
// (MCP 2025-11-25 ``ElicitRequestFormParams``; Codex passes its
// McpServerElicitationRequestParams verbatim, Claude's bridge maps its
// snake_case fields onto the same names).
//
// Self-contained: this component owns its entire body including the action
// row (Cancel / Decline / Submit).

import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import { canStealFocus } from '../../../../../utils/focusGuard'
import { usePendingRequestSubmitShortcut } from '../../../../../composables/usePendingRequestSubmitShortcut'
import { usePendingRequestDraft } from '../../../../../composables/usePendingRequestDraft'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
    sessionId: { type: String, required: true },
})
const emit = defineEmits(['submit'])

const cancelButtonId = useId()
const declineButtonId = useId()
const submitButtonId = useId()

// Base id for the per-field label elements of multiselect groups, referenced
// by each group's aria-labelledby (same pattern as RequestUserInputBody).
// Keyed by field index, not property name: a name containing whitespace
// would yield an invalid HTML id and break the space-separated reference.
const fieldLabelIdBase = useId()
function fieldLabelId(fieldIndex) {
    return `${fieldLabelIdBase}-f${fieldIndex}`
}

// Wire params.
const input = computed(() => props.pendingRequest.tool_input || {})
const serverName = computed(() => input.value.serverName || 'unknown server')
const message = computed(() => input.value.message || '')
const schema = computed(() => input.value.requestedSchema || {})
const requiredSet = computed(() => new Set(schema.value.required || []))

function classify(spec) {
    if (spec.type === 'boolean') return 'boolean'
    if (spec.type === 'number' || spec.type === 'integer') return 'number'
    if (spec.type === 'array') return 'multiselect' // items.enum / items.anyOf|oneOf
    if (spec.type === 'string' && (spec.enum || spec.oneOf)) return 'select'
    if (spec.type === 'string') return 'text'
    return 'unsupported' // rendered as a read-only notice, never blocks submit
}

// Normalised options for select/multiselect:
// enum:[v] (+ enumNames:[n]) → [{value: v, label: n||v}]
// oneOf/anyOf:[{const, title}] → [{value: const, label: title}]
// Non-array variants and entries without a string ``const`` are treated as
// absent — third-party schemas are untrusted input, never a crash source.
function optionsFor(spec) {
    if (Array.isArray(spec.items?.enum)) {
        return spec.items.enum.map((v) => ({ value: v, label: v }))
    }
    if (Array.isArray(spec.enum)) {
        const names = Array.isArray(spec.enumNames) ? spec.enumNames : []
        return spec.enum.map((v, i) => ({ value: v, label: names[i] || v }))
    }
    const variants = [spec.oneOf, spec.items?.anyOf, spec.items?.oneOf].find(Array.isArray) || []
    return variants
        .filter((o) => o && typeof o.const === 'string')
        .map((o) => ({ value: o.const, label: o.title || o.const }))
}

// One entry per schema property. ``options`` is precomputed for select/
// multiselect kinds so the template and the submit mapping share the exact
// same (index-stable) list. Each raw spec is normalised to a plain object
// first (JSON Schema boolean schemas ``true``/``false``, null, arrays, and
// other junk all fall through to 'unsupported' instead of crashing), and a
// select/multiselect whose resolved options list is empty (free-form array,
// malformed enum) is downgraded to 'unsupported' too — a required field
// rendered as an empty group would otherwise dead-lock the Submit button.
const fields = computed(() =>
    Object.entries(schema.value.properties || {}).map(([name, rawSpec]) => {
        const spec = rawSpec && typeof rawSpec === 'object' && !Array.isArray(rawSpec) ? rawSpec : {}
        let kind = classify(spec)
        let options = null
        if (kind === 'select' || kind === 'multiselect') {
            options = optionsFor(spec)
            if (!options.length) {
                kind = 'unsupported'
                options = null
            }
        }
        return {
            name,
            spec,
            kind,
            label: spec.title || name,
            required: requiredSet.value.has(name),
            options,
        }
    }))

// Maps a string format to an <input> type. Anything unrecognised falls back
// to plain text.
function inputTypeFor(spec) {
    switch (spec.format) {
        case 'email': return 'email'
        case 'uri': return 'url'
        case 'date': return 'date'
        case 'date-time': return 'datetime-local'
        default: return 'text'
    }
}

// Per-field reactive store, keyed by property name. Shapes by kind:
// boolean → Boolean; number/text → String (raw control value); select → the
// String INDEX into field.options (wa-option values can't safely hold
// arbitrary strings with spaces, so we index and map back on read);
// multiselect → Array of real option values.
const values = ref({})

function resetState() {
    // Null-prototype: property names come from an untrusted schema, and a
    // field literally named "__proto__" would otherwise hit the Object
    // prototype accessor instead of storing a value.
    const initial = Object.create(null)
    for (const field of fields.value) {
        if (field.kind === 'boolean') {
            initial[field.name] = field.spec.default === true
        } else if (field.kind === 'number') {
            initial[field.name] = field.spec.default !== undefined ? String(field.spec.default) : ''
        } else if (field.kind === 'select') {
            const idx = field.options.findIndex((o) => o.value === field.spec.default)
            initial[field.name] = idx >= 0 ? String(idx) : ''
        } else if (field.kind === 'multiselect') {
            initial[field.name] = Array.isArray(field.spec.default) ? [...field.spec.default] : []
        } else if (field.kind === 'text') {
            initial[field.name] = field.spec.default !== undefined && field.spec.default !== null
                ? String(field.spec.default) : ''
        }
        // unsupported: no state — never read, never written.
    }
    values.value = initial
}
resetState()

function toggleMultiselect(name, optionValue, checked) {
    const arr = values.value[name] || []
    values.value[name] = checked
        ? (arr.includes(optionValue) ? arr : [...arr, optionValue])
        : arr.filter((v) => v !== optionValue)
}

// Template ref + focus for the primary action (Submit), same
// canStealFocus()-gated pattern as the sibling bodies. A Submit still gated
// by unmet required fields is a native disabled button and won't actually
// receive focus — that's fine, the outline class just marks intent.
const submitButtonRef = ref(null)
function focusSubmit() {
    nextTick(() => {
        if (!canStealFocus()) return
        submitButtonRef.value?.focus()
    })
}
onMounted(focusSubmit)

// Reset per-field state and re-focus when a new request takes over the slot.
watch(() => props.pendingRequest?.request_id, () => {
    resetState()
    focusSubmit()
})

// Per-field validity. Booleans and unsupported fields never block submit.
function fieldValid(field) {
    const raw = values.value[field.name]
    switch (field.kind) {
        case 'boolean':
            return true
        case 'number': {
            if (raw === '' || raw === undefined || raw === null) return !field.required
            const num = Number(raw)
            if (!Number.isFinite(num)) return false
            if (field.spec.type === 'integer' && !Number.isInteger(num)) return false
            if (field.spec.minimum !== undefined && num < field.spec.minimum) return false
            if (field.spec.maximum !== undefined && num > field.spec.maximum) return false
            return true
        }
        case 'text': {
            const text = (raw || '').toString()
            if (!text) return !field.required
            if (field.spec.minLength !== undefined && text.length < field.spec.minLength) return false
            if (field.spec.maxLength !== undefined && text.length > field.spec.maxLength) return false
            return true
        }
        case 'select':
            return !field.required || (raw !== '' && raw !== undefined && raw !== null)
        case 'multiselect': {
            const arr = Array.isArray(raw) ? raw : []
            if (arr.length === 0) return !field.required
            const { minItems, maxItems } = field.spec
            if (minItems !== undefined && arr.length < minItems) return false
            if (maxItems !== undefined && arr.length > maxItems) return false
            return true
        }
        case 'unsupported':
        default:
            return true
    }
}

const canSubmit = computed(() => fields.value.every(fieldValid))

function collectContent() {
    const content = {}
    for (const field of fields.value) {
        const raw = values.value[field.name]
        if (field.kind === 'unsupported') continue
        if (field.kind === 'boolean') {
            // A checkbox always has a definite state — always include it.
            content[field.name] = Boolean(raw)
            continue
        }
        if (field.kind === 'select') {
            if (raw === '' || raw === undefined || raw === null) continue
            const opt = field.options[Number(raw)]
            if (opt) content[field.name] = opt.value
            continue
        }
        if (field.kind === 'multiselect') {
            const arr = Array.isArray(raw) ? raw : []
            if (arr.length) content[field.name] = arr
            continue
        }
        if (raw === undefined || raw === null || raw === '') continue
        if (field.kind === 'number') {
            // Number(), not parseInt(): fieldValid already guarantees
            // integerness for type:"integer", and parseInt mis-parses
            // scientific notation ('1e2' → 1 instead of 100).
            const num = Number(raw)
            if (Number.isFinite(num)) content[field.name] = num
        } else {
            content[field.name] = raw
        }
    }
    return content
}

function submit() {
    if (props.isResponding || !canSubmit.value) return
    emit('submit', { tool_name: 'elicitationForm', action: 'accept', content: collectContent() })
}
function decline() {
    if (props.isResponding) return
    emit('submit', { tool_name: 'elicitationForm', action: 'decline' })
}
function cancel() {
    if (props.isResponding) return
    emit('submit', { tool_name: 'elicitationForm', action: 'cancel' })
}

usePendingRequestSubmitShortcut((e) => {
    if (!canSubmit.value) return
    e.preventDefault()
    e.stopPropagation()
    submit()
}, () => props.isResponding)

// Persist the half-filled form so a page reload doesn't lose it.
usePendingRequestDraft({
    sessionId: () => props.sessionId,
    pendingRequest: () => props.pendingRequest,
    isResponding: () => props.isResponding,
    collect: () => ({ values: { ...values.value } }),
    apply: (state) => {
        const stored = state.values
        if (!stored) return
        // Rebuild the null-prototype store field by field: property names come
        // from an untrusted schema, and only the fields this schema declares
        // may be restored (same guard as resetState).
        const restored = Object.create(null)
        for (const field of fields.value) {
            // 'unsupported' fields hold no state at all (see resetState).
            if (field.kind === 'unsupported') continue
            restored[field.name] = Object.prototype.hasOwnProperty.call(stored, field.name)
                ? stored[field.name]
                : values.value[field.name]
        }
        values.value = restored
    },
})
</script>

<template>
    <div v-scroll-shadow class="elicitation-form-body">
        <div class="elicit-section">
            <div class="elicit-summary">
                <span class="elicit-summary-label">MCP form</span>
                <wa-badge variant="neutral">{{ serverName }}</wa-badge>
            </div>
            <div v-if="message" class="elicit-reason">
                <span>{{ message }}</span>
            </div>
        </div>

        <div v-if="fields.length" class="elicit-fields">
            <div v-for="(field, fieldIndex) in fields" :key="field.name" class="elicit-field">
                <!-- Text / number / select use Web Awesome's own label/hint
                     attributes (associated to the control inside its shadow
                     DOM) and its native required asterisk semantics. -->
                <wa-input
                    v-if="field.kind === 'text'"
                    size="small"
                    :label="field.label"
                    :hint="field.spec.description || ''"
                    :required="field.required"
                    :type="inputTypeFor(field.spec)"
                    :minlength="field.spec.minLength"
                    :maxlength="field.spec.maxLength"
                    :value.prop="values[field.name]"
                    :disabled="isResponding"
                    @input="values[field.name] = $event.target.value"
                ></wa-input>

                <wa-input
                    v-else-if="field.kind === 'number'"
                    size="small"
                    :label="field.label"
                    :hint="field.spec.description || ''"
                    :required="field.required"
                    type="number"
                    :step="field.spec.type === 'integer' ? 1 : 'any'"
                    :min="field.spec.minimum"
                    :max="field.spec.maximum"
                    :value.prop="values[field.name]"
                    :disabled="isResponding"
                    @input="values[field.name] = $event.target.value"
                ></wa-input>

                <wa-select
                    v-else-if="field.kind === 'select'"
                    size="small"
                    :label="field.label"
                    :hint="field.spec.description || ''"
                    :required="field.required"
                    :value.prop="values[field.name]"
                    :disabled="isResponding"
                    @change="values[field.name] = $event.target.value"
                >
                    <wa-option v-for="(opt, idx) in field.options" :key="idx" :value="String(idx)">{{ opt.label }}</wa-option>
                </wa-select>

                <!-- Boolean: wa-checkbox renders its own label (default slot)
                     and hint; keep our required marker in the label. -->
                <wa-checkbox
                    v-else-if="field.kind === 'boolean'"
                    :hint="field.spec.description || ''"
                    :checked="values[field.name]"
                    :disabled="isResponding"
                    @change="values[field.name] = $event.target.checked"
                >
                    {{ field.label }}<span v-if="field.required" class="elicit-required" aria-hidden="true"> *</span>
                </wa-checkbox>

                <!-- Multiselect: checkbox group labelled via aria-labelledby
                     (same pattern as RequestUserInputBody's question groups). -->
                <div
                    v-else-if="field.kind === 'multiselect'"
                    class="elicit-multiselect-group"
                    role="group"
                    :aria-labelledby="fieldLabelId(fieldIndex)"
                >
                    <span :id="fieldLabelId(fieldIndex)" class="elicit-label">
                        {{ field.label }}<span v-if="field.required" class="elicit-required" aria-hidden="true"> *</span>
                    </span>
                    <div class="elicit-multiselect">
                        <wa-checkbox
                            v-for="(opt, idx) in field.options"
                            :key="idx"
                            :checked="values[field.name]?.includes(opt.value)"
                            :disabled="isResponding"
                            @change="toggleMultiselect(field.name, opt.value, $event.target.checked)"
                        >{{ opt.label }}</wa-checkbox>
                    </div>
                    <p v-if="field.spec.description" class="elicit-help">{{ field.spec.description }}</p>
                </div>

                <template v-else-if="field.kind === 'unsupported'">
                    <p class="elicit-unsupported">
                        Unsupported field type for "{{ field.name }}".
                    </p>
                    <p v-if="field.spec.description" class="elicit-help">{{ field.spec.description }}</p>
                </template>
            </div>
        </div>

        <div class="elicit-actions">
            <wa-button
                :id="cancelButtonId"
                variant="neutral"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="cancel"
            >
                <wa-icon slot="start" name="ban" variant="classic"></wa-icon>
                Cancel
            </wa-button>
            <AppTooltip :for="cancelButtonId">Dismiss the form without answering.</AppTooltip>

            <wa-button
                :id="declineButtonId"
                variant="danger"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="decline"
            >
                <wa-icon slot="start" name="xmark" variant="classic"></wa-icon>
                Decline
            </wa-button>
            <AppTooltip :for="declineButtonId">Refuse to provide this information.</AppTooltip>

            <wa-button
                :id="submitButtonId"
                ref="submitButtonRef"
                class="auto-focused"
                variant="brand"
                size="small"
                :disabled="isResponding || !canSubmit"
                @click="submit"
            >
                <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                Submit
            </wa-button>
            <AppTooltip :for="submitButtonId">Send the form to the MCP server.</AppTooltip>
        </div>
    </div>
</template>

<style scoped>
.elicitation-form-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    flex: 1;
    min-height: 0;
    overflow-y: auto;
}

.elicit-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-s);
}

.elicit-summary {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.elicit-summary-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}

.elicit-reason {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text);
    font-size: var(--wa-font-size-m);
}

.elicit-fields {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.elicit-field {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.elicit-label {
    font-size: var(--wa-font-size-s);
    font-weight: 600;
    color: var(--wa-color-text);
}

.elicit-required {
    color: var(--wa-color-danger-fill-loud);
}

.elicit-help {
    margin: 0;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.elicit-multiselect-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.elicit-multiselect {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.elicit-unsupported {
    margin: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.elicit-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}

/* Always show the focus outline on the Submit button. Default
   :focus-visible would skip mouse and programmatic focus, which hides the
   indicator we want for this primary action.
   We use :focus-within (not :focus) because wa-button delegates focus to an
   inner element in its shadow DOM; the host doesn't carry :focus, but the
   browser keeps :focus-within accurate via activeElement. */
wa-button.auto-focused:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}
</style>
