import { computed } from 'vue'

// Preserve the rendered value while hidden. Reading visibility before the getter
// drops its live dependencies, so store updates no longer run presentation work.
// Reopening restores those dependencies and computes the current value.
export function useVisibleComputed(active) {
    return (getter, initial) => computed(previous => {
        if (active.value) return getter(previous)
        return previous === undefined ? initial : previous
    })
}
