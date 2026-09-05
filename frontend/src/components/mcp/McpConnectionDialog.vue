<script setup>
/**
 * McpConnectionDialog - review a pending external MCP request, or inspect and
 * rename an existing connection.
 *
 * Mounted by McpManager with a `:key` per row, so one instance always shows
 * one entry and the local form state needs no reset.
 *
 * `open` is deliberately NOT a static attribute: wa-dialog only runs `show()`
 * — the show animation and the `wa-show`/`wa-after-show` events — when `open`
 * flips AFTER its first render (`watch('open', { waitUntilFirstUpdate: true })`).
 * A dialog rendered already-open just calls `showModal()` and never fires
 * `wa-after-show`, which is where focus management lives. So: render closed,
 * then open once the element has updated. Closing follows the same path —
 * `visible = false` lets the dialog animate out and emit `wa-after-hide`,
 * which is when the parent may unmount us.
 *
 * `client_name` and `redirect_uri` come from an unauthenticated OAuth client
 * registration — text interpolation ONLY, never v-html.
 */
import { ref, computed, onMounted } from 'vue'
import { useMcpStore } from '../../stores/mcp'
import HelpFeatureLink from '../help/HelpFeatureLink.vue'

const props = defineProps({ entry: { type: Object, required: true }, review: Boolean })
const emit = defineEmits(['close'])

const store = useMcpStore()
const dialogRef = ref(null)
const firstInput = ref(null)
const submitButton = ref(null)
const visible = ref(false)
const name = ref(props.entry.name || '')
const code = ref('')
const busy = ref(false)
const formId = 'mcp-connection-detail-form'
const title = computed(() => (props.review ? 'Review MCP connection' : 'MCP connection details'))
const status = computed(() => (props.entry.revoked ? 'Revoked' : props.entry.active ? 'Active' : 'Awaiting OAuth completion'))

function moment(iso) {
    return iso ? new Date(iso).toLocaleString() : ''
}

// wa-button does not expose `form` as a property, and the footer button sits
// outside the <form> — the documented ProjectEditDialog pattern.
onMounted(async () => {
    submitButton.value?.setAttribute('form', formId)
    await dialogRef.value?.updateComplete
    visible.value = true
})

function focus() {
    firstInput.value?.focus()
    const length = firstInput.value?.value?.length || 0
    firstInput.value?.setSelectionRange?.(length, length)
}

async function act(action) {
    busy.value = true
    const ok = await store.act(action, { id: props.entry.id, name: name.value.trim(), code: code.value.trim() })
    busy.value = false
    if (ok) visible.value = false
}
</script>

<template>
    <wa-dialog
        ref="dialogRef" :open="visible" :label="title"
        style="--width: min(560px, calc(100vw - 2rem))"
        @wa-after-show.self="focus"
        @wa-after-hide.self="emit('close')"
    >
        <div slot="label" class="mcp-dialog-title">
            <span>{{ title }}</span>
            <HelpFeatureLink help-key="external-mcp" label="What is external MCP?" />
        </div>

        <form :id="formId" class="mcp-detail" @submit.prevent="act(review ? 'approve' : 'rename')">
            <div class="mcp-detail__client">{{ entry.client_name || 'External MCP client' }}</div>

            <wa-callout v-if="store.error || store.loadError" variant="danger" size="small">
                {{ store.error || store.loadError }}
            </wa-callout>

            <template v-if="review">
                <wa-callout variant="warning" size="small">
                    <strong>Full access.</strong>
                    This connection can read and change your TwiCC data, send messages,
                    and manage sessions and shares.
                </wa-callout>
                <wa-input
                    ref="firstInput" class="mcp-detail__code"
                    label="Verification code from the connecting browser"
                    :value="code" @input="code = $event.target.value"
                    required maxlength="8"
                    autocomplete="off" autocapitalize="characters" spellcheck="false"
                ></wa-input>
                <wa-input
                    label="Connection name (optional)" placeholder="ChatGPT"
                    :value="name" @input="name = $event.target.value" maxlength="80"
                ></wa-input>
            </template>
            <wa-input
                v-else ref="firstInput"
                label="Connection name (optional)" placeholder="External MCP"
                :value="name" @input="name = $event.target.value" maxlength="80"
                :disabled="entry.revoked"
            ></wa-input>

            <dl class="mcp-detail__facts">
                <template v-if="!review">
                    <dt>Status</dt><dd>{{ status }}</dd>
                    <dt>Created</dt><dd>{{ moment(entry.created_at) }}</dd>
                    <dt>Last used</dt><dd>{{ entry.last_used_at ? moment(entry.last_used_at) : 'Not yet used' }}</dd>
                </template>
                <dt>Client ID</dt><dd class="mcp-detail__identifier">{{ entry.client_id }}</dd>
                <template v-if="review">
                    <dt>Redirect URL</dt><dd class="mcp-detail__identifier">{{ entry.redirect_uri }}</dd>
                    <dt>Requested</dt><dd>{{ moment(entry.created_at) }}</dd>
                    <dt>Expires</dt><dd>{{ moment(entry.expires_at) }}</dd>
                </template>
            </dl>
        </form>

        <div slot="footer" class="mcp-detail__actions">
            <wa-button variant="neutral" appearance="outlined" @click="visible = false">Close</wa-button>
            <wa-button v-if="review" variant="danger" appearance="outlined" :disabled="busy" @click="act('refuse')">
                Refuse
            </wa-button>
            <wa-button
                v-else-if="!entry.revoked"
                variant="danger" appearance="outlined" :disabled="busy" @click="act('revoke')"
            >Revoke</wa-button>
            <wa-button v-if="review || !entry.revoked" ref="submitButton" type="submit" variant="brand" :disabled="busy">
                {{ review ? 'Authorize' : 'Apply' }}
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.mcp-dialog-title {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--wa-space-3xs);
}

.mcp-detail {
    display: grid;
    gap: var(--wa-space-m);
}

/* The identity the client declares, above the name the owner chooses. */
.mcp-detail__client {
    font-weight: var(--wa-font-weight-semibold);
    overflow-wrap: anywhere;
}

/* An 8-character uppercase hex code, shown letter-spaced by the waiting page
   — read it the same way here. */
.mcp-detail__code::part(input) {
    font-family: var(--wa-font-family-code, monospace);
    letter-spacing: 0.2em;
    text-transform: uppercase;
}

.mcp-detail__facts {
    display: grid;
    gap: var(--wa-space-2xs);
    margin: 0;
}
.mcp-detail__facts dt {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
.mcp-detail__facts dd {
    margin: 0 0 var(--wa-space-xs);
}
.mcp-detail__facts dd:last-child {
    margin-bottom: 0;
}
.mcp-detail__identifier {
    overflow-wrap: anywhere;
    font-size: var(--wa-font-size-s);
}

.mcp-detail__actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-xs);
}
</style>
