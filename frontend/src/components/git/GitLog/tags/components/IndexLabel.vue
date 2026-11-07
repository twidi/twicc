<script setup lang="ts">
import GitIcon from './GitIcon.vue'
</script>

<template>
  <span class="indexLabel">
    {{ $t('git.index.label') }}
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
