<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

const props = defineProps({
    result: { type: [Object, String, Array], required: true },
})

const markdownSource = computed(() => {
    const r = props.result
    const content = typeof r === 'string' ? r : r?.content
    if (typeof content !== 'string' || !content) return null
    return content
})
</script>

<template>
    <MarkdownContent v-if="markdownSource" :source="markdownSource" :show-toolbar="false" />
</template>
