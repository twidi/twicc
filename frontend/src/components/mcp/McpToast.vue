<script setup>
defineProps({ count: Number })
function open() { window.dispatchEvent(new CustomEvent('twicc:open-mcp-manager')) }
</script>
<template><div><p>External MCP access needs your approval.</p><wa-button size="small" @click="open">Review connections</wa-button></div></template>
