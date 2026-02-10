<script setup lang="ts">
import GitIcon from './GitIcon.vue'
import styles from './IndexLabel.module.scss'
</script>

<template>
  <span :class="styles.indexLabel">
    index
  </span>
  <GitIcon />
</template>
