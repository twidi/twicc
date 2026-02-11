<script setup lang="ts">
import GitIcon from './GitIcon.vue'
</script>

<template>
  <span class="indexLabel">
    index
  </span>
  <GitIcon />
</template>

<style scoped>
.indexLabel {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
