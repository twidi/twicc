import { computed, type ComputedRef } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Commit } from '../types'
import type { GraphColumnState } from '../graph/GraphMatrixBuilder/types'
import { placeholderCommits, placeholderColumns } from '../graph/placeholderData'

export interface PlaceholderDatum {
  commit: Commit
  columns: GraphColumnState[]
}

export interface PlaceholderData {
  placeholderData: ComputedRef<PlaceholderDatum[]>
}

/**
 * Provides placeholder data for table rows when no real commits are available.
 *
 * Each placeholder datum pairs a fake commit with its corresponding column
 * states, with `isPlaceholderSkeleton` set to `true` on every column.
 */
export function usePlaceholderData(): PlaceholderData {
  const { t } = useI18n()
  const placeholderData = computed(() => {
    return placeholderCommits.map((commit, i) => ({
      commit: { ...commit, message: commit.message ? t('git.panel.empty') : '' },
      columns: placeholderColumns[i].map(col => ({
        ...col,
        isPlaceholderSkeleton: true,
      })),
    }))
  })

  return {
    placeholderData,
  }
}
