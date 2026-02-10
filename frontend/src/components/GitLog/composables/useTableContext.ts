import { inject } from 'vue'
import { TABLE_CONTEXT_KEY, type TableContextBag } from './keys'

export function useTableContext(): TableContextBag {
  const ctx = inject(TABLE_CONTEXT_KEY)
  if (!ctx) {
    throw new Error('useTableContext must be used within a GitLog component')
  }
  return ctx
}
