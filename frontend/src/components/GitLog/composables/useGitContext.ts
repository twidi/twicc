import { inject } from 'vue'
import { GIT_CONTEXT_KEY, type GitContextBag } from './keys'

export function useGitContext(): GitContextBag {
  const ctx = inject(GIT_CONTEXT_KEY)
  if (!ctx) {
    throw new Error('useGitContext must be used within a GitLog component')
  }
  return ctx
}
