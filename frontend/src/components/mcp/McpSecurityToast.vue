<script setup>
defineProps({ item: { type: Object, default: null } })
function review() { window.dispatchEvent(new CustomEvent('twicc:open-mcp-manager')) }
</script>

<template>
    <div>
        <p>{{ $t('mcp.securityToast.paused') }}</p>
        <wa-button size="small" variant="brand" @click="review">{{ $t('mcp.manager.review') }}</wa-button>
    </div>
</template>
