<script setup lang="ts">
import { computed, type CSSProperties, type PropType } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTheme } from '../../composables/useTheme'
import type { Commit } from '../../types'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps({
  commit: { type: Object as PropType<Commit>, required: true },
  colour: { type: String, required: false },
})

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { getTooltipBackground } = useTheme()
const { t, locale } = useI18n()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const tooltipVars = computed<CSSProperties>(() => ({
  '--tooltip-border-color': props.colour,
  '--tooltip-background-color': getTooltipBackground(props.commit),
}))

const parentsText = computed(() =>
  props.commit.parents.length > 0
    ? new Intl.ListFormat(locale.value, { style: 'long', type: 'conjunction' }).format(props.commit.parents)
    : t('git.commit.tooltip.none'),
)

const childrenText = computed(() =>
  props.commit.children.length > 0
    ? new Intl.ListFormat(locale.value, { style: 'long', type: 'conjunction' }).format(props.commit.children)
    : t('git.commit.tooltip.none'),
)
</script>

<template>
  <div
    :style="tooltipVars"
    class="tooltip"
  >
    <div>
      <p class="label">{{ $t('git.commit.tooltip.hash') }}</p>
      <p class="text">{{ commit.hash }}</p>
    </div>

    <div>
      <p class="label">{{ $t('git.commit.tooltip.parents') }}</p>
      <p class="text">{{ parentsText }}</p>
    </div>

    <div>
      <p class="label">{{ $t('git.commit.tooltip.children') }}</p>
      <p class="text">{{ childrenText }}</p>
    </div>

    <div>
      <p class="label">{{ $t('git.commit.tooltip.branchTip') }}</p>
      <p class="text">{{ commit.isBranchTip ? $t('git.commit.tooltip.yes') : $t('git.commit.tooltip.no') }}</p>
    </div>
  </div>
</template>

<style scoped>
.tooltip {
  padding: .5rem;
  border-radius: 8px;
  color: var(--git-log-text-color);
  border: 2px solid var(--tooltip-border-color);
  background: var(--tooltip-background-color);
  display: flex;
  flex-direction: column;
}

.label {
  font-weight: 600;
  display: inline-block;
  margin: 0 .25rem 0 0;
}

.text {
  display: inline;
}
</style>
