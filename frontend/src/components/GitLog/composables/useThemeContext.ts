import { inject } from 'vue'
import { THEME_CONTEXT_KEY, type ThemeContextBag } from './keys'

export function useThemeContext(): ThemeContextBag {
  const ctx = inject(THEME_CONTEXT_KEY)
  if (!ctx) {
    throw new Error('useThemeContext must be used within a GitLog component')
  }
  return ctx
}
