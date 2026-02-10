import { inject } from 'vue'
import { GRAPH_CONTEXT_KEY, type GraphContextBag } from './keys'

export function useGraphContext(): GraphContextBag {
  const ctx = inject(GRAPH_CONTEXT_KEY)
  if (!ctx) {
    throw new Error('useGraphContext must be used within a GitLog component')
  }
  return ctx
}
