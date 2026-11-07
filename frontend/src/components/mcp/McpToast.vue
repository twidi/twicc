<script setup>
/**
 * McpToast - actionable toast body for a pending external MCP connection
 * request. PeerToastContent is the reference: same structure (icon line, quiet
 * detail lines, outlined actions pushed to the end) so both read alike.
 *
 * No dismiss action: a pending request has no display timeout (design §7).
 * McpManager owns the toast's lifetime and clears it as soon as the request is
 * approved, refused or expired, on every connected device.
 *
 * `client_name` comes from an unauthenticated OAuth client registration —
 * text interpolation ONLY, never v-html.
 */
const props = defineProps({
    requestId: { type: String, default: '' },
    clientName: { type: String, default: '' },
    /** Notivue item reference — passed by CustomNotification. */
    item: { type: Object, default: null },
})

function review() {
    window.dispatchEvent(new CustomEvent('twicc:open-mcp-manager', { detail: { requestId: props.requestId } }))
}
</script>

<template>
    <div class="mcp-toast-content">
        <span class="mcp-toast-line">
            <wa-icon name="plug" class="mcp-toast-icon"></wa-icon>
            <i18n-t keypath="mcp.toast.request" scope="global" tag="span" class="mcp-toast-sentence">
                <template #client><strong>{{ clientName || $t('mcp.toast.externalClient') }}</strong></template>
            </i18n-t>
        </span>
        <span class="mcp-toast-hint">{{ $t('mcp.toast.hint') }}</span>
        <div class="mcp-toast-actions">
            <wa-button size="small" variant="brand" appearance="outlined" @click="review">{{ $t('mcp.manager.review') }}</wa-button>
        </div>
    </div>
</template>

<style scoped>
.mcp-toast-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-xs);
}

.mcp-toast-line {
    display: flex;
    align-items: center;
    font-size: var(--wa-font-size-m);
    min-width: 0;
}
.mcp-toast-sentence {
    min-width: 0;
}
.mcp-toast-sentence strong {
    display: inline-block;
    max-width: 100%;
    vertical-align: bottom;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.mcp-toast-icon {
    margin-right: var(--wa-space-xs);
    color: var(--wa-color-brand-on-quiet);
    flex: none;
}

.mcp-toast-hint {
    font-size: 0.8rem;
    opacity: 0.75;
}

.mcp-toast-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-xs);
}
</style>
