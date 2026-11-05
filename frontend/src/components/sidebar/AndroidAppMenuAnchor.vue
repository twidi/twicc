<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { registerAndroidMenuAnchor } from '../../utils/androidMenuPlacement.js'

const anchor = ref(null)
let release
onMounted(() => { release = registerAndroidMenuAnchor(anchor.value) })
onBeforeUnmount(() => { release?.() })
</script>

<template>
    <span ref="anchor" class="android-app-menu-anchor" />
</template>

<style scoped>
.android-app-menu-anchor {
    display: contents;
}
</style>
