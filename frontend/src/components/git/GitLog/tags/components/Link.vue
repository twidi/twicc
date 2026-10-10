<script setup lang="ts">

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

defineProps({
  href: { type: String, required: false },
  text: { type: String, required: true },
})

</script>

<template>
  <a
    :href="href"
    target="_blank"
    rel="noopener noreferrer"
    class="link"
  >
    {{ text }}
  </a>
</template>

<style scoped>
.link {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-decoration: none;
  color: var(--git-log-text-color);
}
</style>
